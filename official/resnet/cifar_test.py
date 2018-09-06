# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
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

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from tempfile import mkstemp

import numpy as np
import tensorflow as tf  # pylint: disable=g-bad-import-order
from official.resnet import cifar_main
from official.utils.testing import integration

tf.logging.set_verbosity(tf.logging.ERROR)

_BATCH_SIZE = 32
_HEIGHT = 32
_WIDTH = 32
_NUM_CHANNELS = 3

class BaseTest(tf.test.TestCase):
  """Tests for the cifar version of Resnet.
  """

  @classmethod
  def setUpClass(cls):  # pylint: disable=invalid-name
    super(BaseTest, cls).setUpClass()
    cifar_main.define_cifar_flags()

  def tearDown(self):
    super(BaseTest, self).tearDown()
    tf.gfile.DeleteRecursively(self.get_temp_dir())

  def test_dataset_input_fn(self):
    fake_data = bytearray()
    fake_data.append(7)
    for i in range(_NUM_CHANNELS):
      for _ in range(_HEIGHT * _WIDTH):
        fake_data.append(i)

    _, filename = mkstemp(dir=self.get_temp_dir())
    data_file = open(filename, 'wb')
    data_file.write(fake_data)
    data_file.close()

    fake_dataset = tf.data.FixedLengthRecordDataset(
        filename, cifar_main._RECORD_BYTES)  # pylint: disable=protected-access
    fake_dataset = fake_dataset.map(
<<<<<<< HEAD:official/resnet/cifar_test.py
        lambda val: cifar_main.parse_record(val, False))
=======
        lambda val: cifar10_main.parse_record(val, False, tf.float32))
>>>>>>> 7b3046768d2d6ea3f306b6ee7b62c02c9ca128a1:official/resnet/cifar10_test.py
    image, label = fake_dataset.make_one_shot_iterator().get_next()

    self.assertAllEqual(label.shape, ())
    self.assertAllEqual(image.shape, (_HEIGHT, _WIDTH, _NUM_CHANNELS))

    with self.test_session() as sess:
      image, label = sess.run([image, label])

      self.assertEqual(label, 7)

      for row in image:
        for pixel in row:
          self.assertAllClose(pixel, np.array([-1.225, 0., 1.225]), rtol=1e-3)

  def cifar_model_fn_helper(self, mode, resnet_version, dtype):
    input_fn = cifar_main.get_synth_input_fn(dtype)
    dataset = input_fn(True, '', _BATCH_SIZE)
    iterator = dataset.make_initializable_iterator()
    features, labels = iterator.get_next()
    spec = cifar_main.cifar_model_fn(
        features, labels, mode, {
            'dtype': dtype,
            'resnet_size': 32,
            'data_format': 'channels_last',
            'batch_size': _BATCH_SIZE,
            'resnet_version': resnet_version,
            'loss_scale': 128 if dtype == tf.float16 else 1,
            'fine_tune': False,
        })

    predictions = spec.predictions
    self.assertAllEqual(predictions['probabilities'].shape,
                        (_BATCH_SIZE, 10))
    self.assertEqual(predictions['probabilities'].dtype, tf.float32)
    self.assertAllEqual(predictions['classes'].shape, (_BATCH_SIZE,))
    self.assertEqual(predictions['classes'].dtype, tf.int64)

    if mode != tf.estimator.ModeKeys.PREDICT:
      loss = spec.loss
      self.assertAllEqual(loss.shape, ())
      self.assertEqual(loss.dtype, tf.float32)

    if mode == tf.estimator.ModeKeys.EVAL:
      eval_metric_ops = spec.eval_metric_ops
      self.assertAllEqual(eval_metric_ops['accuracy'][0].shape, ())
      self.assertAllEqual(eval_metric_ops['accuracy'][1].shape, ())
      self.assertEqual(eval_metric_ops['accuracy'][0].dtype, tf.float32)
      self.assertEqual(eval_metric_ops['accuracy'][1].dtype, tf.float32)

  def test_cifar_model_fn_train_mode_v1(self):
    self.cifar_model_fn_helper(tf.estimator.ModeKeys.TRAIN, resnet_version=1,
                                 dtype=tf.float32)

  def test_cifar_model_fn_trainmode__v2(self):
    self.cifar_model_fn_helper(tf.estimator.ModeKeys.TRAIN, resnet_version=2,
                                 dtype=tf.float32)

  def test_cifar_model_fn_eval_mode_v1(self):
    self.cifar_model_fn_helper(tf.estimator.ModeKeys.EVAL, resnet_version=1,
                                 dtype=tf.float32)

  def test_cifar_model_fn_eval_mode_v2(self):
    self.cifar_model_fn_helper(tf.estimator.ModeKeys.EVAL, resnet_version=2,
                                 dtype=tf.float32)

  def test_cifar_model_fn_predict_mode_v1(self):
    self.cifar_model_fn_helper(tf.estimator.ModeKeys.PREDICT,
                                 resnet_version=1, dtype=tf.float32)

  def test_cifar_model_fn_predict_mode_v2(self):
    self.cifar_model_fn_helper(tf.estimator.ModeKeys.PREDICT,
                                 resnet_version=2, dtype=tf.float32)

  def _test_cifarmodel_shape(self, resnet_version):
    batch_size = 135
    num_classes = 246

    model = cifar_main.CifarModel(32, data_format='channels_last',
                                      num_classes=num_classes,
                                      resnet_version=resnet_version)
    fake_input = tf.random_uniform([batch_size, _HEIGHT, _WIDTH, _NUM_CHANNELS])
    output = model(fake_input, training=True)

    self.assertAllEqual(output.shape, (batch_size, num_classes))

  def test_cifarmodel_shape_v1(self):
    self._test_cifarmodel_shape(resnet_version=1)

  def test_cifarmodel_shape_v2(self):
    self._test_cifarmodel_shape(resnet_version=2)

  def test_cifar_end_to_end_synthetic_v1(self):
    integration.run_synthetic(
        main=cifar_main.run_cifar, tmp_root=self.get_temp_dir(),
        extra_flags=['-resnet_version', '1']
    )

  def test_cifar_end_to_end_synthetic_v2(self):
    integration.run_synthetic(
        main=cifar_main.run_cifar, tmp_root=self.get_temp_dir(),
        extra_flags=['-resnet_version', '2']
    )


if __name__ == '__main__':
  tf.test.main()
