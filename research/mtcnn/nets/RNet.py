# Copyright 2018 The TensorFlow Authors All Rights Reserved.
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

import numpy as np
import tensorflow as tf
from tensorflow.contrib import slim

from nets.AbstractFaceDetector import AbstractFaceDetector
from utils.prelu import prelu

class RNet(AbstractFaceDetector):

	def __init__(self, batch_size = 1):
		AbstractFaceDetector.__init__(self)
		self._network_size = 24
		self._network_name = 'RNet'
		self._batch_size = batch_size

	def batch_size(self):
		return(self._batch_size)

	def _setup_basic_network(self, inputs):
		self._end_points = {}

		with slim.arg_scope([slim.conv2d],
                        	activation_fn = prelu,
                        	weights_initializer=slim.xavier_initializer(),
                        	biases_initializer=tf.zeros_initializer(),
                        	weights_regularizer=slim.l2_regularizer(0.0005),                        
                        	padding='valid'):

			end_point = 'conv1'
        		net = slim.conv2d(inputs, num_outputs=28, kernel_size=[3,3], stride=1, scope=end_point)
			self._end_points[end_point] = net

			end_point = 'pool1'
        		net = slim.max_pool2d(net, kernel_size=[3, 3], stride=2, scope=end_point, padding='SAME')
			self._end_points[end_point] = net

			end_point = 'conv2'
        		net = slim.conv2d(net, num_outputs=48, kernel_size=[3,3], stride=1, scope=end_point)
			self._end_points[end_point] = net

			end_point = 'pool2'
        		net = slim.max_pool2d(net, kernel_size=[3,3], stride=2, scope=end_point)
			self._end_points[end_point] = net

			end_point = 'conv3'
        		net = slim.conv2d(net, num_outputs=64, kernel_size=[2,2], stride=1, scope=end_point)
			self._end_points[end_point] = net

        		fc_flatten = slim.flatten(net)

			end_point = 'fc1'
        		fc1 = slim.fully_connected(fc_flatten, num_outputs=128, scope=end_point, activation_fn=prelu)
			self._end_points[end_point] = fc1

        		#batch*2
			end_point = 'cls_fc'
        		class_probability = slim.fully_connected(fc1, num_outputs=2, scope=end_point, activation_fn=tf.nn.softmax)
			self._end_points[end_point] = class_probability

        		#batch*4
			end_point = 'bbox_fc'
        		bounding_box_predictions = slim.fully_connected(fc1, num_outputs=4, scope=end_point, activation_fn=None)
			self._end_points[end_point] = bounding_box_predictions

        		#batch*10
			end_point = 'landmark_fc'
        		landmark_predictions = slim.fully_connected(fc1, num_outputs=10, scope=end_point, activation_fn=None)
			self._end_points[end_point] = landmark_predictions

			return(class_probability, bounding_box_predictions, landmark_predictions)

	def setup_training_network(self, inputs):
		return(self.setup_basic_network(inputs))

	def setup_inference_network(self, checkpoint_path):
        	graph = tf.Graph()
        	with graph.as_default():
            		self._input_batch = tf.placeholder(tf.float32, shape=[self.batch_size(), self.network_size(), self.network_size(), 3], name='input_batch')            		
            		self._output_class_probability, self._output_bounding_box, self._output_landmarks = self._setup_basic_network(self._input_batch)

            		self._session = tf.Session(config=tf.ConfigProto(allow_soft_placement=True, gpu_options=tf.GPUOptions(allow_growth=True)))
			self._load_model_from(checkpoint_path)

	def detect(self, data_batch):
        	scores = []
        	batch_size = self.batch_size()

        	minibatch = []
        	cur = 0

	        n = data_batch.shape[0]
	        while cur < n:
	            minibatch.append(data_batch[cur:min(cur + batch_size, n), :, :, :])
	            cur += batch_size

	        class_probability_list = []
	        bounding_box_list = []
	        landmark_list = []
	        for idx, data in enumerate(minibatch):
	            m = data.shape[0]
	            real_size = self.batch_size()

	            if m < batch_size:
	                keep_inds = np.arange(m)

	                gap = self.batch_size() - m
	                while gap >= len(keep_inds):
	                    gap -= len(keep_inds)
	                    keep_inds = np.concatenate((keep_inds, keep_inds))
	                if gap != 0:
	                    keep_inds = np.concatenate((keep_inds, keep_inds[:gap]))
	                data = data[keep_inds]
	                real_size = m

	            class_probabilities, bounding_boxes, landmarks = self._session.run([self._output_class_probability, self._output_bounding_box, self._output_landmarks], feed_dict={self._input_batch: data})

	            class_probability_list.append(class_probabilities[:real_size])
	            bounding_box_list.append(bounding_boxes[:real_size])
	            landmark_list.append(landmarks[:real_size])

	        return( np.concatenate(class_probability_list, axis=0), np.concatenate(bounding_box_list, axis=0), np.concatenate(landmark_list, axis=0) )
	
