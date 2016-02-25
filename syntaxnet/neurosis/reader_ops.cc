/* Copyright 2016 Google Inc. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
==============================================================================*/

#include <math.h>
#include <unordered_map>
#include <memory>
#include <string>
#include <vector>

#include "neurosis/base.h"
#include "neurosis/feature_extractor.h"
#include "neurosis/parser_state.h"
#include "neurosis/parser_state_context.h"
#include "neurosis/parser_transitions.h"
#include "neurosis/sentence.pb.h"
#include "neurosis/shared_store.h"
#include "neurosis/sparse.pb.h"
#include "neurosis/task_context.h"
#include "neurosis/task_spec.pb.h"
#include "third_party/eigen3/unsupported/Eigen/CXX11/Tensor"
#include "tensorflow/core/framework/op_kernel.h"
#include "tensorflow/core/framework/tensor.h"
#include "tensorflow/core/framework/tensor_shape.h"
#include "tensorflow/core/lib/core/status.h"
#include "tensorflow/core/lib/io/inputbuffer.h"
#include "tensorflow/core/lib/io/table.h"
#include "tensorflow/core/lib/io/table_options.h"
#include "tensorflow/core/lib/strings/stringprintf.h"
#include "tensorflow/core/platform/env.h"

using tensorflow::DEVICE_CPU;
using tensorflow::DT_FLOAT;
using tensorflow::DT_INT32;
using tensorflow::DT_INT64;
using tensorflow::DT_STRING;
using tensorflow::DataType;
using tensorflow::OpKernel;
using tensorflow::OpKernelConstruction;
using tensorflow::OpKernelContext;
using tensorflow::Tensor;
using tensorflow::TensorShape;
using tensorflow::error::OUT_OF_RANGE;
using tensorflow::errors::InvalidArgument;

namespace neurosis {

class ParsingReader : public OpKernel {
 public:
  explicit ParsingReader(OpKernelConstruction *context) : OpKernel(context) {
    string file_path, corpus_name;
    OP_REQUIRES_OK(context, context->GetAttr("task_context", &file_path));
    OP_REQUIRES_OK(context, context->GetAttr("feature_size", &feature_size_));
    OP_REQUIRES_OK(context, context->GetAttr("batch_size", &max_batch_size_));
    OP_REQUIRES_OK(context, context->GetAttr("corpus_name", &corpus_name));
    OP_REQUIRES_OK(context, context->GetAttr("arg_prefix", &arg_prefix_));

    // Reads task context from file.
    string data;
    OP_REQUIRES_OK(context, ReadFileToString(tensorflow::Env::Default(),
                                             file_path, &data));
    OP_REQUIRES(context,
                TextFormat::ParseFromString(data, task_context_.mutable_spec()),
                InvalidArgument("Could not parse task context at ", file_path));

    // Set up the batch reader.
    sentence_batch_.reset(
        new SentenceBatch(max_batch_size_, corpus_name));
    sentence_batch_->Init(&task_context_);

    // Set up the parsing features and transition system.
    states_.resize(max_batch_size_);
    workspaces_.resize(max_batch_size_);
    features_.reset(new ParserEmbeddingFeatureExtractor(arg_prefix_));
    features_->Setup(&task_context_);
    transition_system_.reset(ParserTransitionSystem::Create(task_context_.Get(
        features_->GetParamName("transition_system"), "arc-standard")));
    transition_system_->Setup(&task_context_);
    features_->Init(&task_context_);
    features_->RequestWorkspaces(&workspace_registry_);
    transition_system_->Init(&task_context_);
    string label_map_path =
        TaskContext::InputFile(*task_context_.GetInput("label-map"));
    label_map_ = SharedStoreUtils::GetWithDefaultName<TermFrequencyMap>(
        label_map_path, 0, 0);

    // Checks number of feature groups matches the task context.
    const int required_size = features_->embedding_dims().size();
    OP_REQUIRES(
        context, feature_size_ == required_size,
        InvalidArgument("Task context requires feature_size=", required_size));
  }

  ~ParsingReader() override { SharedStore::Release(label_map_); }

  // Creates a new ParserState if there's another sentence to be read.
  void AdvanceSentence(int index) {
    states_[index].reset();
    if (sentence_batch_->AdvanceSentence(index)) {
      states_[index].reset(new ParserState(
          sentence_batch_->sentence(index),
          transition_system_->NewTransitionState(true), label_map_));
      workspaces_[index].Reset(workspace_registry_);
      features_->Preprocess(&workspaces_[index], states_[index].get());
    }
  }

  void Compute(OpKernelContext *context) override {
    MutexLock lock(mu_);

    // Advances states to the next positions.
    PerformActions(context);

    // Advances any final states to the next sentences.
    for (int i = 0; i < max_batch_size_; ++i) {
      if (state(i) == nullptr) continue;

      // Switches to the next sentence if we're at a final state.
      while (transition_system_->IsFinalState(*state(i))) {
        VLOG(2) << "Advancing sentence " << i;
        AdvanceSentence(i);
        if (state(i) == nullptr) break;  // EOF has been reached
      }
    }

    // Rewinds if no states remain in the batch (we need to re-wind the corpus).
    if (sentence_batch_->size() == 0) {
      ++num_epochs_;
      LOG(INFO) << "Starting epoch " << num_epochs_;
      sentence_batch_->Rewind();
      for (int i = 0; i < max_batch_size_; ++i) AdvanceSentence(i);
    }

    // Create the outputs for each feature space.
    vector<Tensor *> feature_outputs(features_->NumEmbeddings());
    for (size_t i = 0; i < feature_outputs.size(); ++i) {
      OP_REQUIRES_OK(context, context->allocate_output(
                                  i, TensorShape({sentence_batch_->size(),
                                                  features_->FeatureSize(i)}),
                                  &feature_outputs[i]));
    }

    // Populate feature outputs.
    for (int i = 0, index = 0; i < max_batch_size_; ++i) {
      if (states_[i] == nullptr) continue;

      // Extract features from the current parser state, and fill up the
      // available batch slots.
      std::vector<std::vector<SparseFeatures>> features =
          features_->ExtractSparseFeatures(workspaces_[i], *states_[i]);

      for (size_t feature_space = 0; feature_space < features.size();
           ++feature_space) {
        int feature_size = features[feature_space].size();
        CHECK(feature_size == features_->FeatureSize(feature_space));
        auto features_output = feature_outputs[feature_space]->matrix<string>();
        for (int k = 0; k < feature_size; ++k) {
          features_output(index, k) =
              features[feature_space][k].SerializeAsString();
        }
      }
      ++index;
    }

    // Return the number of epochs.
    Tensor *epoch_output;
    OP_REQUIRES_OK(context, context->allocate_output(
                                feature_size_, TensorShape({}), &epoch_output));
    auto num_epochs = epoch_output->scalar<int32>();
    num_epochs() = num_epochs_;

    // Create outputs specific to this reader.
    AddAdditionalOutputs(context);
  }

 protected:
  // Peforms any relevant actions on the parser states, typically either
  // the gold action or a predicted action from decoding.
  virtual void PerformActions(OpKernelContext *context) = 0;

  // Adds outputs specific to this reader starting at additional_output_index().
  virtual void AddAdditionalOutputs(OpKernelContext *context) const = 0;

  // Returns the output type specification of the this base class.
  std::vector<DataType> default_outputs() const {
    std::vector<DataType> output_types(feature_size_, DT_STRING);
    output_types.push_back(DT_INT32);
    return output_types;
  }

  // Accessors.
  int max_batch_size() const { return max_batch_size_; }
  int batch_size() const { return sentence_batch_->size(); }
  int additional_output_index() const { return feature_size_ + 1; }
  ParserState *state(int i) const { return states_[i].get(); }
  const ParserTransitionSystem &transition_system() const {
    return *transition_system_.get();
  }

  // Parser task context.
  const TaskContext &task_context() const { return task_context_; }

  const string &arg_prefix() const { return arg_prefix_; }

 private:
  // Task context used to configure this op.
  TaskContext task_context_;

  // Prefix for context parameters.
  string arg_prefix_;

  // Mutex to synchronize access to Compute.
  Mutex mu_;

  // How many times the document source has been rewinded.
  int num_epochs_ = 0;

  // How many sentences this op can be processing at any given time.
  int max_batch_size_ = 1;

  // Number of feature groups in the brain parser features.
  int feature_size_ = -1;

  // Batch of sentences, and the corresponding parser states.
  std::unique_ptr<SentenceBatch> sentence_batch_;

  // Batch: ParserState objects.
  std::vector<std::unique_ptr<ParserState>> states_;

  // Batch: WorkspaceSet objects.
  std::vector<WorkspaceSet> workspaces_;

  // Dependency label map used in transition system.
  const TermFrequencyMap *label_map_;

  // Transition system.
  std::unique_ptr<ParserTransitionSystem> transition_system_;

  // Typed feature extractor for embeddings.
  std::unique_ptr<ParserEmbeddingFeatureExtractor> features_;

  // Internal workspace registry for use in feature extraction.
  WorkspaceRegistry workspace_registry_;

  TF_DISALLOW_COPY_AND_ASSIGN(ParsingReader);
};

class GoldParseReader : public ParsingReader {
 public:
  explicit GoldParseReader(OpKernelConstruction *context)
      : ParsingReader(context) {
    // Sets up number and type of inputs and outputs.
    std::vector<DataType> output_types = default_outputs();
    output_types.push_back(DT_INT32);
    OP_REQUIRES_OK(context, context->MatchSignature({}, output_types));
  }

 private:
  // Always performs the next gold action for each state.
  void PerformActions(OpKernelContext *context) override {
    for (int i = 0; i < max_batch_size(); ++i) {
      if (state(i) != nullptr) {
        transition_system().PerformAction(
            transition_system().GetNextGoldAction(*state(i)), state(i));
      }
    }
  }

  // Adds the list of gold actions for each state as an additional output.
  void AddAdditionalOutputs(OpKernelContext *context) const override {
    Tensor *actions_output;
    OP_REQUIRES_OK(context, context->allocate_output(
                                additional_output_index(),
                                TensorShape({batch_size()}), &actions_output));

    // Add all gold actions for non-null states as an additional output.
    auto gold_actions = actions_output->vec<int32>();
    for (int i = 0, batch_index = 0; i < max_batch_size(); ++i) {
      if (state(i) != nullptr) {
        const int gold_action =
            transition_system().GetNextGoldAction(*state(i));
        gold_actions(batch_index++) = gold_action;
      }
    }
  }

  TF_DISALLOW_COPY_AND_ASSIGN(GoldParseReader);
};

REGISTER_KERNEL_BUILDER(Name("GoldParseReader").Device(DEVICE_CPU),
                        GoldParseReader);

// DecodedParseReader parses sentences using transition scores computed
// by a TensorFlow network. This op additionally computes a token correctness
// evaluation metric which can be used to select hyperparameter settings and
// training stopping point.
//
// The notion of correct token is determined by the transition system, e.g.
// a tagger will return POS tag accuracy, while an arc-standard parser will
// return UAS.
//
// Which tokens should be scored is controlled by the '<arg_prefix>_scoring'
// task parameter.  Possible values are
//   - 'default': skips tokens with only punctuation in the tag name.
//   - 'conllx': skips tokens with only punctuation in the surface form.
//   - 'ignore_parens': same as conllx, but skipping parentheses as well.
//   - '': scores all tokens.
class DecodedParseReader : public ParsingReader {
 public:
  explicit DecodedParseReader(OpKernelConstruction *context)
      : ParsingReader(context) {
    // Sets up number and type of inputs and outputs.
    std::vector<DataType> output_types = default_outputs();
    output_types.push_back(DT_INT32);
    output_types.push_back(DT_STRING);
    OP_REQUIRES_OK(context, context->MatchSignature({DT_FLOAT}, output_types));

    // Gets scoring parameters.
    scoring_type_ = task_context().Get(
        tensorflow::strings::StrCat(arg_prefix(), "_scoring"), "");
  }

 private:
  // Tallies the # of correct and incorrect tokens for a given ParserState.
  void ComputeTokenAccuracy(const ParserState &state) {
    for (int i = 0; i < state.sentence().token_size(); ++i) {
      const Token &token = state.GetToken(i);
      if (utils::PunctuationUtil::ScoreToken(token.word(), token.tag(),
                                             scoring_type_)) {
        ++num_tokens_;
        if (state.IsTokenCorrect(i)) ++num_correct_;
      }
    }
  }

  // Performs the allowed action with the highest score on the given state.
  // Also records the accuracy whenver a terminal action is taken.
  void PerformActions(OpKernelContext *context) override {
    auto scores_matrix = context->input(0).matrix<float>();
    num_tokens_ = 0;
    num_correct_ = 0;
    for (int i = 0, batch_index = 0; i < max_batch_size(); ++i) {
      ParserState *state = this->state(i);
      if (state != nullptr) {
        int best_action = 0;
        float best_score = -INFINITY;
        for (int action = 0; action < scores_matrix.dimension(1); ++action) {
          float score = scores_matrix(batch_index, action);
          if (score > best_score &&
              transition_system().IsAllowedAction(action, *state)) {
            best_action = action;
            best_score = score;
          }
        }
        transition_system().PerformAction(best_action, state);

        // Update the # of scored correct tokens if this is the last state
        // in the sentence and save the annotated document.
        if (transition_system().IsFinalState(*state)) {
          ComputeTokenAccuracy(*state);
          documents_.emplace_back(state->sentence());
          state->AddParseToDocument(&documents_.back());
        }
        ++batch_index;
      }
    }
  }

  // Adds the evaluation metrics and annotated documents as additional outputs,
  // if there were any terminal states.
  void AddAdditionalOutputs(OpKernelContext *context) const override {
    Tensor *counts_output;
    OP_REQUIRES_OK(context,
                   context->allocate_output(additional_output_index(),
                                            TensorShape({2}), &counts_output));
    auto eval_metrics = counts_output->vec<int32>();
    eval_metrics(0) = num_tokens_;
    eval_metrics(1) = num_correct_;

    // Output annotated documents for each state.
    Tensor *annotated_output;
    OP_REQUIRES_OK(context,
                   context->allocate_output(
                       additional_output_index() + 1,
                       TensorShape({static_cast<int64>(documents_.size())}),
                       &annotated_output));

    auto document_output = annotated_output->vec<string>();
    for (size_t i = 0; i < documents_.size(); ++i) {
      document_output(i) = documents_[i].SerializeAsString();
    }
    documents_.clear();
  }

  // State for eval metric computation.
  int num_tokens_ = 0;
  int num_correct_ = 0;

  // Parameter for deciding which tokens to score.
  string scoring_type_;

  mutable vector<Sentence> documents_;

  TF_DISALLOW_COPY_AND_ASSIGN(DecodedParseReader);
};

REGISTER_KERNEL_BUILDER(Name("DecodedParseReader").Device(DEVICE_CPU),
                        DecodedParseReader);

class WordEmbeddingInitializer : public OpKernel {
 public:
  explicit WordEmbeddingInitializer(OpKernelConstruction *context)
      : OpKernel(context) {
    string file_path, data;
    OP_REQUIRES_OK(context, context->GetAttr("task_context", &file_path));
    OP_REQUIRES_OK(context, ReadFileToString(tensorflow::Env::Default(),
                                             file_path, &data));
    OP_REQUIRES(context,
                TextFormat::ParseFromString(data, task_context_.mutable_spec()),
                InvalidArgument("Could not parse task context at ", file_path));
    OP_REQUIRES_OK(context, context->GetAttr("vectors", &vectors_path_));
    OP_REQUIRES_OK(context,
                   context->GetAttr("embedding_init", &embedding_init_));

    // Sets up number and type of inputs and outputs.
    OP_REQUIRES_OK(context, context->MatchSignature({}, {DT_FLOAT}));
  }

  void Compute(OpKernelContext *context) override {
    // Loads words from vocabulary with mapping to ids.
    string path = TaskContext::InputFile(*task_context_.GetInput("word-map"));
    const TermFrequencyMap *word_map =
        SharedStoreUtils::GetWithDefaultName<TermFrequencyMap>(path, 0, 0);
    unordered_map<string, int64> vocab;
    for (int i = 0; i < word_map->Size(); ++i) {
      vocab[word_map->GetTerm(i)] = i;
    }

    // Creates a reader pointing to a local copy of the vectors recordio.
    string tmp_vectors_path;
    OP_REQUIRES_OK(context, CopyToTmpPath(vectors_path_, &tmp_vectors_path));
    ProtoRecordReader reader(tmp_vectors_path);

    // Loads the embedding vectors into a matrix.
    Tensor *embedding_matrix = nullptr;
    TokenEmbedding embedding;
    while (reader.Read(&embedding) == tensorflow::Status::OK()) {
      if (embedding_matrix == nullptr) {
        const int embedding_size = embedding.vector().values_size();
        OP_REQUIRES_OK(
            context, context->allocate_output(
                         0, TensorShape({word_map->Size() + 3, embedding_size}),
                         &embedding_matrix));
        embedding_matrix->matrix<float>()
            .setRandom<Eigen::internal::NormalRandomGenerator<float>>();
        // TODO(chrisalberti): why won't this compile??
        //embedding_matrix->matrix<float>() =
        //    embedding_matrix->matrix<float>().scale<float>(
        //        embedding_init_ / sqrt(embedding_size));
      }
      if (vocab.find(embedding.token()) != vocab.end()) {
        SetNormalizedRow(embedding.vector(), vocab[embedding.token()],
                         embedding_matrix);
      }
    }
  }

 private:
  // Sets embedding_matrix[row] to a normalized version of the given vector.
  void SetNormalizedRow(const TokenEmbedding::Vector &vector, const int row,
                        Tensor *embedding_matrix) {
    float norm = 0.0f;
    for (int col = 0; col < vector.values_size(); ++col) {
      float val = vector.values(col);
      norm += val * val;
    }
    norm = sqrt(norm);
    for (int col = 0; col < vector.values_size(); ++col) {
      embedding_matrix->matrix<float>()(row, col) = vector.values(col) / norm;
    }
  }

  // Copies the file at source_path to a temporary file and sets tmp_path to the
  // temporary file's location. This is helpful since reading from non local
  // files with a record reader can be very slow.
  static tensorflow::Status CopyToTmpPath(const string &source_path,
                                          string *tmp_path) {
    // Opens source file.
    tensorflow::RandomAccessFile *source_file;
    TF_RETURN_IF_ERROR(tensorflow::Env::Default()->NewRandomAccessFile(
        source_path, &source_file));
    std::unique_ptr<tensorflow::RandomAccessFile> source_file_deleter(
        source_file);

    // Creates destination file.
    tensorflow::WritableFile *target_file;
    *tmp_path = tensorflow::strings::Printf(
        "/tmp/%d.%lld", getpid(), tensorflow::Env::Default()->NowMicros());
    TF_RETURN_IF_ERROR(
        tensorflow::Env::Default()->NewWritableFile(*tmp_path, &target_file));
    std::unique_ptr<tensorflow::WritableFile> target_file_deleter(target_file);

    // Performs copy.
    tensorflow::Status s;
    const size_t kBytesToRead = 10 << 20;  // 10MB at a time.
    string scratch;
    scratch.resize(kBytesToRead);
    for (uint64 offset = 0; s.ok(); offset += kBytesToRead) {
      tensorflow::StringPiece data;
      s.Update(source_file->Read(offset, kBytesToRead, &data, &scratch[0]));
      target_file->Append(data);
    }
    if (s.code() == OUT_OF_RANGE) {
      return tensorflow::Status::OK();
    } else {
      return s;
    }
  }

  // Task context used to configure this op.
  TaskContext task_context_;

  // Embedding vectors that are not found in the input sstable are initialized
  // randomly from a normal distribution with zero mean and
  //   std dev = embedding_init_ / sqrt(embedding_size).
  float embedding_init_ = 1.f;

  // Path to recordio with word embedding vectors.
  string vectors_path_;
};

REGISTER_KERNEL_BUILDER(Name("WordEmbeddingInitializer").Device(DEVICE_CPU),
                        WordEmbeddingInitializer);

}  // namespace neurosis
