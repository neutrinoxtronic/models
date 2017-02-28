# Copyright 2016 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
r"""Downloads and converts Flowers data to TFRecords of TF-Example protos.

This module downloads the Flowers data, uncompresses it, reads the files
that make up the Flowers data and creates two TFRecord datasets: one for train
and one for test. Each TFRecord dataset is comprised of a set of TF-Example
protocol buffers, each of which contain a single image and label.

The script should take about a minute to run.

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math
import os
import random
import sys
import numpy as np
import pickle

import tensorflow as tf

from datasets import dataset_utils

# The URL where the Flowers data can be downloaded.
_DATA_URL = 'http://download.tensorflow.org/example_images/flower_photos.tgz'

# The number of images in the validation set.
_NUM_VALIDATION = 1

# Seed for repeatability.
_RANDOM_SEED = 10

# The number of shards per dataset split.
_NUM_SHARDS = 1

# The number of shards per dataset split.
_NUM_SHARDS = 1

# The number train IDs.
_NUM_TRAIN_IDs = 400


class ImageReader(object):
  """Helper class that provides TensorFlow image coding utilities."""

  def __init__(self):
    # Initializes function that decodes RGB JPEG data.
    self._decode_jpeg_data = tf.placeholder(dtype=tf.string)
    self._decode_jpeg = tf.image.decode_jpeg(self._decode_jpeg_data, channels=3)
    # self._decode_numpy = tf.decode_raw(self._decode_jpeg_data)

  def read_image_dims(self, sess, image_data):
    image = self.decode_jpeg(sess, image_data)
    return image.shape[0], image.shape[1]

  def decode_jpeg(self, sess, image_data):
    image = sess.run(self._decode_jpeg,
                     feed_dict={self._decode_jpeg_data: image_data})
    assert len(image.shape) == 3
    assert image.shape[2] == 3
    return image
  # def decode_numpy(self, sess, image_data):
  #   image = sess.run(self._decode_numpy,
  #                    feed_dict={self._decode_jpeg_data: image_data})
  #   assert len(image.shape) == 3
  #   assert image.shape[2] == 3
  #   return image


def _get_filenames_and_classes(dataset_dir):
  """Returns a list of filenames and inferred class names.

  Args:
    dataset_dir: A directory containing a set of subdirectories representing
      class names. Each subdirectory should contain PNG or JPG encoded images.

  Returns:
    A list of image file paths, relative to `dataset_dir` and the list of
    subdirectories, representing class names.
  """
  dataset_root = os.path.join(dataset_dir)
  directories = []
  class_names = []
  # os.listdir returns all the files and directories in the input argument.
  for filename in os.listdir(dataset_root):
    path = os.path.join(dataset_root, filename)
    if os.path.isdir(path):
      directories.append(path)
      class_names.append(filename)

  # For training
  directories = directories[0:_NUM_TRAIN_IDs]
  class_names = class_names[0:_NUM_TRAIN_IDs]

  # The directories which contain sound and mouth numpy files for each clip
  numpy_dirnames = []
  for directory in directories:
    dir_clips = os.path.join(directory, 'val')
    for dir_clip in os.listdir(dir_clips):
      path_clip_per_clips_per_subjects = os.path.join(dir_clips, dir_clip)
      numpy_dirnames.append(path_clip_per_clips_per_subjects)

  return numpy_dirnames, sorted(class_names)


def _get_dataset_filename(dataset_dir, split_name, shard_id):
  output_filename = 'numpy_%s_%05d-of-%05d.tfrecord' % (
      split_name, shard_id, _NUM_SHARDS)
  return os.path.join(dataset_dir, output_filename)


def _convert_dataset(split_name, dirnames, class_names_to_ids, dataset_dir):
  """Converts the given filenames to a TFRecord dataset.

  Args:
    split_name: The name of the dataset, either 'train' or 'validation'.
    filenames: A list of absolute paths to png or jpg images.
    class_names_to_ids: A dictionary from class names (strings) to ids
      (integers).
    dataset_dir: The directory where the converted datasets are stored.
  """
  assert split_name in ['train', 'validation']

  num_per_shard = int(math.ceil(len(dirnames) / float(_NUM_SHARDS)))

  with tf.Graph().as_default():
    image_reader = ImageReader()

    # Why tf.Session('')?
    with tf.Session('') as sess:

      for shard_id in range(_NUM_SHARDS):
        output_filename = _get_dataset_filename(
            dataset_dir, split_name, shard_id)

        with tf.python_io.TFRecordWriter(output_filename) as tfrecord_writer:
          start_ndx = shard_id * num_per_shard
          end_ndx = min((shard_id+1) * num_per_shard, len(dirnames))
          for i in range(start_ndx, end_ndx):
            sys.stdout.write('\r>> Converting pair %d/%d shard %d' % (
                i+1, len(dirnames), shard_id))
            sys.stdout.flush()

            # Read activation of the mouth
            with open(os.path.join(dirnames[i], 'activation.dat'), "rb") as f:
              activation = pickle.load(f)

            """
            Read the cube of the speech features for the whole clip.
            """

            # # tf.gfile.FastGFile is the file I/O wrappers.
            # speech_data = tf.gfile.FastGFile(filename, 'r').read()

            # Only static features
            speech_data = np.load(os.path.join(dirnames[i], 'sound.npy'))[0,:,:]
            height_speech, width_speech = speech_data.shape
            print(speech_data.shape)

            # Shift speech for 0.5 sec in order to create impostor pairs
            speech_data_imp = np.roll(speech_data, 25, axis=1)

            with open(os.path.join(dirnames[i], 'gray_mouth.dat'), "rb") as f:
              mouth_list = pickle.load(f)
            num_frames = len(mouth_list)
            mouth_cube = np.zeros((num_frames, 47, 73), dtype=np.int)

            for i in range(num_frames):
              if activation[i] == 1:
                mouth_cube[i, :, :] = np.resize(mouth_list[i], (47, 73))

            print(mouth_cube.shape)
            sys.exit(1)

            class_name = os.path.basename(os.path.dirname(filenames[i]))
            class_id = class_names_to_ids[class_name]

            example = dataset_utils.image_to_tfexample(speech_data, mouth_data, image_format, channel_speech, height_speech, width_speech,
                   channel_mouth, height_mouth, width_mouth, label)
            tfrecord_writer.write(example.SerializeToString())

  sys.stdout.write('\n')
  sys.stdout.flush()


def _clean_up_temporary_files(dataset_dir):
  """Removes temporary files used to create the dataset.

  Args:
    dataset_dir: The directory where the temporary files are stored.
  """
  filename = _DATA_URL.split('/')[-1]
  filepath = os.path.join(dataset_dir, filename)
  tf.gfile.Remove(filepath)

  tmp_dir = os.path.join(dataset_dir, 'flower_photos')
  tf.gfile.DeleteRecursively(tmp_dir)


def _dataset_exists(dataset_dir):
  for split_name in ['train', 'validation']:
    for shard_id in range(_NUM_SHARDS):
      output_filename = _get_dataset_filename(
          dataset_dir, split_name, shard_id)
      if not tf.gfile.Exists(output_filename):
        return False
  return True


def run(dataset_dir):
  """Runs the download and conversion operation.

  Args:
    dataset_dir: The dataset directory where the dataset is stored.
  """
  if not tf.gfile.Exists(dataset_dir):
    tf.gfile.MakeDirs(dataset_dir)

  if _dataset_exists(dataset_dir):
    print('Dataset files already exist. Exiting without re-creating them.')
    return

  # dataset_utils.download_and_uncompress_tarball(_DATA_URL, dataset_dir)
  numpy_dirnames, class_names = _get_filenames_and_classes(dataset_dir)
  class_names_to_ids = dict(zip(class_names, range(len(class_names))))

  # Divide into train and test:
  random.seed(_RANDOM_SEED)
  random.shuffle(numpy_dirnames)
  training_filenames = numpy_dirnames[_NUM_VALIDATION:]
  validation_filenames = numpy_dirnames[:_NUM_VALIDATION]


  # First, convert the training and validation sets.
  _convert_dataset('train', training_filenames, class_names_to_ids,
                   dataset_dir)
  _convert_dataset('validation', validation_filenames, class_names_to_ids,
                   dataset_dir)

  # Finally, write the labels file:
  labels_to_class_names = dict(zip(range(len(class_names)), class_names))
  dataset_utils.write_label_file(labels_to_class_names, dataset_dir)


  # _clean_up_temporary_files(dataset_dir)
  # print('\nFinished converting the Flowers dataset!')

