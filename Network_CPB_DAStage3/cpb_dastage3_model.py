import sys

sys.path.append('../Data_Initialization/')
import numpy as np
import tensorflow as tf
import tensorflow.contrib.layers as layers
import Initialization as init
import cpb_dastage3_utils as utils
import time


class cpb_dastage3_model(object):
    def __init__(self, model_name, sess, train_data, val_data, tst_data, reload_path, epoch, num_class, learning_rate,
                 batch_size, img_height, img_width):

        self.sess = sess
        self.source_training_data = train_data[0]
        self.target_training_data = train_data[1]
        self.source_validation_data = val_data
        self.source_test_data = tst_data[0]
        self.target_test_data = tst_data[1]
        self.reload_path = reload_path
        self.eps = epoch
        self.model = model_name
        self.ckptDir = '../checkpoint/' + self.model + '/'
        self.lr = learning_rate
        self.bs = batch_size
        self.img_h = img_height
        self.img_w = img_width
        self.num_class = num_class
        self.plt_epoch = []
        self.plt_source_training_accuracy = []
        self.plt_g_loss = []
        self.plt_df_loss = []
        self.plt_dl_loss = []
        self.plt_cpb_loss = []

        self.build_model()
        self.saveConfiguration()

    def saveConfiguration(self):
        utils.save2file('epoch : %d' % self.eps, self.ckptDir, self.model)
        utils.save2file('model : %s' % self.model, self.ckptDir, self.model)
        utils.save2file('learning rate : %g' % self.lr, self.ckptDir, self.model)
        utils.save2file('batch size : %d' % self.bs, self.ckptDir, self.model)
        utils.save2file('image height : %d' % self.img_h, self.ckptDir, self.model)
        utils.save2file('image width : %d' % self.img_w, self.ckptDir, self.model)
        utils.save2file('num class : %d' % self.num_class, self.ckptDir, self.model)

    def convLayer(self, inputMap, out_channel, ksize, stride, scope_name, padding='SAME'):
        with tf.variable_scope(scope_name):
            conv_weight = tf.get_variable('conv_weight',
                                          [ksize, ksize, inputMap.get_shape()[-1], out_channel],
                                          initializer=layers.variance_scaling_initializer())

            conv_result = tf.nn.conv2d(inputMap, conv_weight, strides=[1, stride, stride, 1], padding=padding)

            tf.summary.histogram('conv_weight', conv_weight)
            tf.summary.histogram('conv_result', conv_result)

        return conv_result

    def deconvLayer(self, inputMap, out_channel, ksize, stride, scope_name, padding='SAME'):
        with tf.variable_scope(scope_name):
            conv_result = tf.layers.conv2d_transpose(inputMap, filters=out_channel, kernel_size=(ksize, ksize),
                                                     strides=(stride, stride), padding=padding, use_bias=False,
                                                     name='conv_result',
                                                     kernel_initializer=layers.variance_scaling_initializer())

            tf.summary.histogram('deconv_result', conv_result)

        return conv_result

    def bnLayer(self, inputMap, scope_name, is_training):
        with tf.variable_scope(scope_name):
            return tf.layers.batch_normalization(inputMap, training=is_training)

    def reluLayer(self, inputMap, scope_name):
        with tf.variable_scope(scope_name):
            return tf.nn.relu(inputMap)

    def lreluLayer(self, inputMap, scope_name):
        with tf.variable_scope(scope_name):
            return tf.nn.leaky_relu(inputMap)

    def avgPoolLayer(self, inputMap, ksize, stride, scope_name, padding='SAME'):
        with tf.variable_scope(scope_name):
            return tf.nn.avg_pool(inputMap, ksize=[1, ksize, ksize, 1], strides=[1, stride, stride, 1], padding=padding)

    def globalPoolLayer(self, inputMap, scope_name):
        with tf.variable_scope(scope_name):
            size = inputMap.get_shape()[1]
            return self.avgPoolLayer(inputMap, size, size, padding='VALID', scope_name=scope_name)

    def flattenLayer(self, inputMap, scope_name):
        with tf.variable_scope(scope_name):
            return tf.layers.flatten(inputMap)

    def fcLayer(self, inputMap, out_channel, scope_name):
        with tf.variable_scope(scope_name):
            in_channel = inputMap.get_shape()[-1]
            fc_weight = tf.get_variable('fc_weight', [in_channel, out_channel],
                                        initializer=layers.variance_scaling_initializer())
            fc_bias = tf.get_variable('fc_bias', [out_channel], initializer=tf.zeros_initializer())

            fc_result = tf.matmul(inputMap, fc_weight) + fc_bias

            tf.summary.histogram('fc_weight', fc_weight)
            tf.summary.histogram('fc_bias', fc_bias)
            tf.summary.histogram('fc_result', fc_result)

        return fc_result

    def residualUnitLayer(self, inputMap, out_channel, ksize, unit_name, down_sampling, is_training, first_conv=False):
        with tf.variable_scope(unit_name):
            in_channel = inputMap.get_shape().as_list()[-1]
            if down_sampling:
                stride = 2
                increase_dim = True
            else:
                stride = 1
                increase_dim = False

            if first_conv:
                conv_layer1 = self.convLayer(inputMap, out_channel, ksize, stride, scope_name='conv_layer1')
            else:
                bn_layer1 = self.bnLayer(inputMap, scope_name='bn_layer1', is_training=is_training)
                relu_layer1 = self.reluLayer(bn_layer1, scope_name='relu_layer1')
                conv_layer1 = self.convLayer(relu_layer1, out_channel, ksize, stride, scope_name='conv_layer1')

            bn_layer2 = self.bnLayer(conv_layer1, scope_name='bn_layer2', is_training=is_training)
            relu_layer2 = self.reluLayer(bn_layer2, scope_name='relu_layer2')
            conv_layer2 = self.convLayer(relu_layer2, out_channel, ksize, stride=1, scope_name='conv_layer2')

            if increase_dim:
                identical_mapping = self.avgPoolLayer(inputMap, ksize=2, stride=2, scope_name='identical_pool')
                identical_mapping = tf.pad(identical_mapping, [[0, 0], [0, 0], [0, 0],
                                                               [(out_channel - in_channel) // 2,
                                                                (out_channel - in_channel) // 2]])
            else:
                identical_mapping = inputMap

            added = tf.add(conv_layer2, identical_mapping)

        return added

    def residualStageLayer(self, inputMap, ksize, out_channel, unit_num, stage_name, down_sampling, first_conv,
                           is_training, reuse=False):
        with tf.variable_scope(stage_name, reuse=reuse):
            _out = inputMap
            _out = self.residualUnitLayer(_out, out_channel, ksize, unit_name='unit_1', down_sampling=down_sampling,
                                          first_conv=first_conv, is_training=is_training)
            for n in range(2, unit_num + 1):
                _out = self.residualUnitLayer(_out, out_channel, ksize, unit_name='unit_' + str(n),
                                              down_sampling=False, first_conv=False, is_training=is_training)

        return _out

    def stage0_Block(self, inputMap, scope_name, out_channel, ksize, reuse=False):
        with tf.variable_scope(scope_name, reuse=reuse):
            stage0_conv = self.convLayer(inputMap, out_channel, ksize=ksize, stride=1, scope_name='stage0_conv')
            stage0_bn = self.bnLayer(stage0_conv, scope_name='stage0_bn', is_training=self.is_training)
            stage0_relu = self.reluLayer(stage0_bn, scope_name='stage0_relu')

        return stage0_relu

    def classifier(self, inputMap, scope_name, is_training, reuse=False):
        with tf.variable_scope(scope_name, reuse=reuse):
            bn = self.bnLayer(inputMap, scope_name='bn', is_training=is_training)
            relu = self.reluLayer(bn, scope_name='relu')
            gap = self.globalPoolLayer(relu, scope_name='gap')
            flatten = self.flattenLayer(gap, scope_name='flatten')
            pred = self.fcLayer(flatten, self.num_class, scope_name='pred')
            pred_softmax = tf.nn.softmax(pred, name='pred_softmax')

        return pred, pred_softmax

    def discriminator_F(self, inputMap, scope_name, is_training, reuse):
        with tf.variable_scope(scope_name, reuse=reuse):
            lrelu1_1 = self.lreluLayer(inputMap, scope_name='lrelu1_1')
            conv1_1 = self.convLayer(lrelu1_1, out_channel=64, ksize=3, stride=1, scope_name='conv1_1')
            bn1_1 = self.bnLayer(conv1_1, scope_name='bn1_1', is_training=is_training)
            lrelu1_2 = self.lreluLayer(bn1_1, scope_name='lrelu1_2')
            conv1_2 = self.convLayer(lrelu1_2, out_channel=64, ksize=3, stride=1, scope_name='conv1_2')

            bn2_1 = self.bnLayer(conv1_2, scope_name='bn2_1', is_training=is_training)
            lrelu2_1 = self.lreluLayer(bn2_1, scope_name='lrelu2_1')
            conv2_1 = self.convLayer(lrelu2_1, out_channel=128, ksize=3, stride=2, scope_name='conv2_1')
            bn2_2 = self.bnLayer(conv2_1, scope_name='bn2_2', is_training=is_training)
            lrelu2_2 = self.lreluLayer(bn2_2, scope_name='lrelu2_2')
            conv2_2 = self.convLayer(lrelu2_2, out_channel=128, ksize=3, stride=1, scope_name='conv2_2')

            bn3_1 = self.bnLayer(conv2_2, scope_name='bn3_1', is_training=is_training)
            lrelu3_1 = self.lreluLayer(bn3_1, scope_name='lrelu3_1')
            conv3_1 = self.convLayer(lrelu3_1, out_channel=256, ksize=3, stride=2, scope_name='conv3_1')
            bn3_2 = self.bnLayer(conv3_1, scope_name='bn3_2', is_training=is_training)
            lrelu3_2 = self.lreluLayer(bn3_2, scope_name='lrelu3_2')
            conv3_2 = self.convLayer(lrelu3_2, out_channel=256, ksize=3, stride=1, scope_name='conv3_2')

            bn4_1 = self.bnLayer(conv3_2, scope_name='bn4_1', is_training=is_training)
            lrelu4_1 = self.lreluLayer(bn4_1, scope_name='lrelu4_1')
            conv_final = self.convLayer(lrelu4_1, out_channel=1, ksize=3, stride=1, scope_name='conv_final')

        return conv_final

    def discriminator_L(self, inputMap, scope_name, reuse):
        with tf.variable_scope(scope_name, reuse=reuse):
            argmax_index = tf.argmax(inputMap, axis=1)
            one_hot = tf.one_hot(argmax_index, depth=self.num_class)

            fc1 = self.fcLayer(inputMap=one_hot, out_channel=128, scope_name='fc1')
            lrelu1 = self.lreluLayer(fc1, scope_name='lrelu1')
            fc2 = self.fcLayer(inputMap=lrelu1, out_channel=256, scope_name='fc2')
            lrelu2 = self.lreluLayer(fc2, scope_name='lrelu2')
            fc3 = self.fcLayer(inputMap=lrelu2, out_channel=512, scope_name='fc3')
            lrelu3 = self.lreluLayer(fc3, scope_name='lrelu3')
            fc4 = self.fcLayer(inputMap=lrelu3, out_channel=1, scope_name='fc4')

        return fc4

    def contentPreservedBlock(self, inputMap, scope_name, is_training):
        with tf.variable_scope(scope_name):
            preFeature = inputMap[0]
            outFeature = inputMap[1]

            input_size = preFeature.get_shape()[-2]
            input_channel = preFeature.get_shape()[-1]

            bn1 = self.bnLayer(outFeature, scope_name='bn1', is_training=is_training)
            relu1 = self.reluLayer(bn1, scope_name='relu1')
            conv1 = self.convLayer(relu1, out_channel=16, ksize=3, stride=1, scope_name='conv1')

            bn2 = self.bnLayer(conv1, scope_name='bn2', is_training=is_training)
            relu2 = self.reluLayer(bn2, scope_name='relu2')
            conv2 = self.convLayer(relu2, out_channel=32, ksize=3, stride=1, scope_name='conv2')

            bn3 = self.bnLayer(conv2, scope_name='bn3', is_training=is_training)
            relu3 = self.reluLayer(bn3, scope_name='relu3')
            conv3 = self.convLayer(relu3, out_channel=64, ksize=3, stride=1, scope_name='conv3')

            bn4 = self.bnLayer(conv3, scope_name='bn4', is_training=is_training)
            relu4 = self.reluLayer(bn4, scope_name='relu4')

            if conv3.get_shape()[-2] == input_size:
                conv4 = self.convLayer(relu4, out_channel=input_channel, ksize=3, stride=1, scope_name='conv4')
            else:
                conv4 = self.deconvLayer(relu4, out_channel=input_channel, ksize=3, stride=2, scope_name='deconv4')

        return conv4

    def task_model(self, inputMap, ksize, stage1_num, stage2_num, stage3_num, out_channel1, out_channel2, out_channel3,
                   is_training, reuse_stage0=False, reuse_stage1=False, reuse_stage2=False, reuse_stage3=False,
                   reuse_classifier=False, extra_stage1_name='', extra_stage2_name='', extra_stage3_name=''):
        stage0 = self.stage0_Block(inputMap=inputMap, scope_name='stage0', out_channel=out_channel1, ksize=ksize,
                                   reuse=reuse_stage0)

        stage1 = self.residualStageLayer(inputMap=stage0, ksize=ksize, out_channel=out_channel1,
                                         unit_num=stage1_num, stage_name='stage1' + extra_stage1_name,
                                         down_sampling=False, first_conv=True, is_training=is_training,
                                         reuse=reuse_stage1)

        stage2 = self.residualStageLayer(inputMap=stage1, ksize=ksize, out_channel=out_channel2,
                                         unit_num=stage2_num, stage_name='stage2' + extra_stage2_name,
                                         down_sampling=True, first_conv=False, is_training=is_training,
                                         reuse=reuse_stage2)

        stage3 = self.residualStageLayer(inputMap=stage2, ksize=ksize, out_channel=out_channel3,
                                         unit_num=stage3_num, stage_name='stage3' + extra_stage3_name,
                                         down_sampling=True, first_conv=False, is_training=is_training,
                                         reuse=reuse_stage3)

        pred, pred_softmax = self.classifier(stage3, scope_name='classifier', is_training=is_training,
                                             reuse=reuse_classifier)

        return pred, pred_softmax, [stage0, stage1, stage2, stage3]

    def build_model(self):
        self.x_source = tf.placeholder(tf.float32, shape=[None, self.img_h, self.img_w, 1], name='x_source')
        tf.summary.image('Image/source/origin', self.x_source)
        self.x_target = tf.placeholder(tf.float32, shape=[None, self.img_h, self.img_w, 1], name='x_target')
        tf.summary.image('Image/target/origin', self.x_target)

        self.y_source = tf.placeholder(tf.int32, shape=[None, self.num_class], name='y_source')
        self.y_target = tf.placeholder(tf.int32, shape=[None, self.num_class], name='y_target')
        self.is_training = tf.placeholder(tf.bool, name='is_training')

        self.pred_source, self.pred_source_softmax, self.feature_lib_source = self.task_model(inputMap=self.x_source,
                                                                                              ksize=3,
                                                                                              stage1_num=3,
                                                                                              stage2_num=3,
                                                                                              stage3_num=3,
                                                                                              out_channel1=16,
                                                                                              out_channel2=32,
                                                                                              out_channel3=64,
                                                                                              is_training=self.is_training,
                                                                                              reuse_stage0=False,
                                                                                              reuse_stage1=False,
                                                                                              reuse_stage2=False,
                                                                                              reuse_stage3=False,
                                                                                              reuse_classifier=False,
                                                                                              extra_stage3_name='_source')

        self.pred_target, self.pred_target_softmax, self.feature_lib_target = self.task_model(inputMap=self.x_target,
                                                                                              ksize=3,
                                                                                              stage1_num=3,
                                                                                              stage2_num=3,
                                                                                              stage3_num=3,
                                                                                              out_channel1=16,
                                                                                              out_channel2=32,
                                                                                              out_channel3=64,
                                                                                              is_training=self.is_training,
                                                                                              reuse_stage0=True,
                                                                                              reuse_stage1=True,
                                                                                              reuse_stage2=True,
                                                                                              reuse_stage3=False,
                                                                                              reuse_classifier=True,
                                                                                              extra_stage3_name='_target')

        self.source_stage3_features = self.feature_lib_source[3]
        self.target_stage3_features = self.feature_lib_target[3]
        self.target_stage2_features = self.feature_lib_target[2]

        self.source_stage3_dis = self.discriminator_F(self.source_stage3_features, scope_name='stage3_discriminator',
                                                      is_training=self.is_training, reuse=False)
        self.target_stage3_dis = self.discriminator_F(self.target_stage3_features, scope_name='stage3_discriminator',
                                                      is_training=self.is_training, reuse=True)

        self.source_label_dis = self.discriminator_L(self.y_source, scope_name='label_discriminator', reuse=False)
        self.target_label_dis = self.discriminator_L(self.pred_target, scope_name='label_discriminator', reuse=True)

        self.cpb_features = self.contentPreservedBlock([self.target_stage2_features, self.target_stage3_features],
                                                       scope_name='stage3_cpb',
                                                       is_training=self.is_training)

        with tf.variable_scope('loss'):
            self.target_stage3_gloss = tf.reduce_mean(
                tf.squared_difference(self.target_stage3_dis, tf.ones_like(self.target_stage3_dis)))

            self.target_label_gloss = tf.reduce_mean(
                tf.nn.sigmoid_cross_entropy_with_logits(logits=self.target_label_dis,
                                                        labels=tf.ones_like(self.target_label_dis)))

            self.source_stage3_dloss = tf.reduce_mean(
                tf.squared_difference(self.source_stage3_dis, tf.ones_like(self.source_stage3_dis)))
            self.target_stage3_dloss = tf.reduce_mean(
                tf.squared_difference(self.target_stage3_dis, tf.zeros_like(self.target_stage3_dis)))

            self.source_label_dloss = tf.reduce_mean(
                tf.nn.sigmoid_cross_entropy_with_logits(logits=self.source_label_dis,
                                                        labels=tf.ones_like(self.source_label_dis)))
            self.target_label_dloss = tf.reduce_mean(
                tf.nn.sigmoid_cross_entropy_with_logits(logits=self.target_label_dis,
                                                        labels=tf.zeros_like(self.target_label_dis)))

            self.d_loss_feature = self.source_stage3_dloss + self.target_stage3_dloss
            self.d_loss_label = self.source_label_dloss + self.target_label_dloss

            self.cpb_loss = tf.reduce_mean(tf.abs(self.target_stage2_features - self.cpb_features))

            self.g_loss = self.target_stage3_gloss + self.target_label_gloss + 10 * self.cpb_loss

        with tf.variable_scope('variables'):
            # 定义参与训练的参数
            self.stage3_target_var = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='stage3_target')
            self.stage3_discriminator_var = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES,
                                                              scope='stage3_discriminator')
            self.stage3_cpb_var = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='stage3_cpb')
            self.label_discriminator_var = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES,
                                                             scope='label_discriminator')

            # 定义需要重载的参数
            self.reload_var_shared = []
            self.reload_var_shared += tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='stage0')
            self.reload_var_shared += tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='stage1')
            self.reload_var_shared += tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='stage2')
            self.reload_var_shared += tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='classifier')

            self.reload_var_unshared_source = {}
            self.reload_var_unshared_target = {}
            source_stage3_var = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='stage3_source')
            target_stage3_var = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='stage3_target')
            for sv in source_stage3_var:
                modified_name = sv.name.replace('_source', '')
                self.reload_var_unshared_source[modified_name[:-2]] = sv
            for tv in target_stage3_var:
                modified_name = tv.name.replace('_target', '')
                self.reload_var_unshared_target[modified_name[:-2]] = tv

        with tf.variable_scope('optimize'):
            with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS, scope='stage3_target')):
                self.encoder_trainOp = tf.train.AdamOptimizer(self.lr, beta1=0.5).minimize(self.g_loss,
                                                                                           var_list=self.stage3_target_var)
            with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS, scope='stage3_discriminator')):
                self.discriminator_feature_trainOp = tf.train.AdamOptimizer(self.lr, beta1=0.5).minimize(
                    self.d_loss_feature,
                    var_list=self.stage3_discriminator_var)
            with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS, scope='label_discriminator')):
                self.discriminator_label_trainOp = tf.train.AdamOptimizer(self.lr, beta1=0.5).minimize(
                    self.d_loss_label,
                    var_list=self.label_discriminator_var)
            with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS, scope='stage3_cpb')):
                self.cpb_trainOp = tf.train.AdamOptimizer(self.lr).minimize(self.cpb_loss, var_list=self.stage3_cpb_var)

        with tf.variable_scope('tfSummary'):
            self.merged = tf.summary.merge_all()
            self.writer = tf.summary.FileWriter(self.ckptDir, self.sess.graph)

        with tf.variable_scope('saver'):
            var_list = tf.trainable_variables()
            g_list = tf.global_variables()
            bn_moving_vars = [g for g in g_list if 'moving_mean' in g.name]
            bn_moving_vars += [g for g in g_list if 'moving_variance' in g.name]
            var_list += bn_moving_vars
            self.saver = tf.train.Saver(var_list=var_list, max_to_keep=self.eps)

        with tf.variable_scope('accuracy'):
            self.distribution_source = [tf.argmax(self.y_source, 1), tf.argmax(self.pred_source_softmax, 1)]
            self.distribution_target = [tf.argmax(self.y_target, 1), tf.argmax(self.pred_target_softmax, 1)]

            self.correct_prediction_source = tf.equal(self.distribution_source[0], self.distribution_source[1])
            self.correct_prediction_target = tf.equal(self.distribution_target[0], self.distribution_target[1])

            self.accuracy_source = tf.reduce_mean(tf.cast(self.correct_prediction_source, 'float'))
            self.accuracy_target = tf.reduce_mean(tf.cast(self.correct_prediction_target, 'float'))

    def f_value(self, matrix):
        f = 0.0
        length = len(matrix[0])
        for i in range(length):
            recall = matrix[i][i] / np.sum([matrix[i][m] for m in range(self.num_class)])
            precision = matrix[i][i] / np.sum([matrix[n][i] for n in range(self.num_class)])
            result = (recall * precision) / (recall + precision)
            f += result
        f *= (2 / self.num_class)

        return f

    def test_procedure(self, test_data, distribution_op, inputX, inputY, mode):
        confusion_matrics = np.zeros([self.num_class, self.num_class], dtype="int")

        tst_batch_num = int(np.ceil(test_data[0].shape[0] / self.bs))
        for step in range(tst_batch_num):
            _testImg = test_data[0][step * self.bs:step * self.bs + self.bs]
            _testLab = test_data[1][step * self.bs:step * self.bs + self.bs]

            matrix_row, matrix_col = self.sess.run(distribution_op, feed_dict={inputX: _testImg,
                                                                               inputY: _testLab,
                                                                               self.is_training: False})
            for m, n in zip(matrix_row, matrix_col):
                confusion_matrics[m][n] += 1

        test_accuracy = float(np.sum([confusion_matrics[q][q] for q in range(self.num_class)])) / float(
            np.sum(confusion_matrics))
        detail_test_accuracy = [confusion_matrics[i][i] / np.sum(confusion_matrics[i]) for i in
                                range(self.num_class)]
        log0 = "Mode: " + mode
        log1 = "Test Accuracy : %g" % test_accuracy
        log2 = np.array(confusion_matrics.tolist())
        log3 = ''
        for j in range(self.num_class):
            log3 += 'category %s test accuracy : %g\n' % (utils.pulmonary_category[j], detail_test_accuracy[j])
        log3 = log3[:-1]
        log4 = 'F_Value : %g\n' % self.f_value(confusion_matrics)

        utils.save2file(log0, self.ckptDir, self.model)
        utils.save2file(log1, self.ckptDir, self.model)
        utils.save2file(log2, self.ckptDir, self.model)
        utils.save2file(log3, self.ckptDir, self.model)
        utils.save2file(log4, self.ckptDir, self.model)

    def getBatchData(self):
        _src_tr_img_batch, _src_tr_lab_batch = init.next_batch(self.source_training_data[0],
                                                               self.source_training_data[1], self.bs)
        _tar_tr_img_batch = init.next_batch_unpaired(self.target_training_data, self.bs)

        feed_dict = {self.x_source: _src_tr_img_batch,
                     self.y_source: _src_tr_lab_batch,
                     self.x_target: _tar_tr_img_batch,
                     self.is_training: True}
        feed_dict_eval = {self.x_source: _src_tr_img_batch,
                          self.y_source: _src_tr_lab_batch,
                          self.x_target: _tar_tr_img_batch,
                          self.is_training: False}

        return feed_dict, feed_dict_eval

    def train(self):
        # 全局初始化
        self.sess.run(tf.global_variables_initializer())
        print('Global Initialization Finish')

        # 重载参数
        self.reload_shared = tf.train.Saver(var_list=self.reload_var_shared)
        self.reload_unshared_source = tf.train.Saver(var_list=self.reload_var_unshared_source)
        self.reload_unshared_target = tf.train.Saver(var_list=self.reload_var_unshared_target)

        self.reload_shared.restore(self.sess, self.reload_path)
        self.reload_unshared_source.restore(self.sess, self.reload_path)
        self.reload_unshared_target.restore(self.sess, self.reload_path)
        print('Restore Parameters Finish')

        # 开始训练
        self.itr_epoch = len(self.source_training_data[0]) // self.bs

        source_training_acc = 0.0
        g_loss = 0.0
        d_loss_feature = 0.0
        d_loss_label = 0.0
        cpb_loss = 0.0

        for e in range(1, self.eps + 1):
            for itr in range(self.itr_epoch):
                for m in range(2):
                    feed_dict_train, feed_dict_eval = self.getBatchData()
                    _, _, _ = self.sess.run(
                        [self.discriminator_feature_trainOp, self.discriminator_label_trainOp,
                         self.cpb_trainOp], feed_dict=feed_dict_train)
                feed_dict_train, feed_dict_eval = self.getBatchData()
                _ = self.sess.run(self.encoder_trainOp, feed_dict=feed_dict_train)

                _source_training_acc, _g_loss, _d_loss_feature, _d_loss_label, _cpb_loss = self.sess.run(
                    [self.accuracy_source, self.g_loss, self.d_loss_feature, self.d_loss_label, self.cpb_loss],
                    feed_dict=feed_dict_train)

                source_training_acc += _source_training_acc
                g_loss += _g_loss
                d_loss_feature += _d_loss_feature
                d_loss_label += _d_loss_label
                cpb_loss += _cpb_loss

            summary = self.sess.run(self.merged, feed_dict=feed_dict_eval)

            source_training_acc = float(source_training_acc / self.itr_epoch)
            g_loss = float(g_loss / self.itr_epoch)
            d_loss_feature = float(d_loss_feature / self.itr_epoch)
            d_loss_label = float(d_loss_label / self.itr_epoch)
            cpb_loss = float(cpb_loss / self.itr_epoch)

            log1 = "Epoch: [%d], Source Training Accuracy: [%g], G Loss: [%g], DF Loss: [%g], DL Loss: [%g], " \
                   "CPB Loss: [%g], Time: [%s]" % (
                       e, source_training_acc, g_loss, d_loss_feature, d_loss_label, cpb_loss, time.ctime(time.time()))

            self.plt_epoch.append(e)
            self.plt_source_training_accuracy.append(source_training_acc)
            self.plt_g_loss.append(g_loss)
            self.plt_df_loss.append(d_loss_feature)
            self.plt_dl_loss.append(d_loss_label)
            self.plt_cpb_loss.append(cpb_loss)

            utils.plotAccuracy(x=self.plt_epoch,
                               y=self.plt_source_training_accuracy,
                               figName=self.model,
                               lineName='source',
                               savePath=self.ckptDir)

            utils.plotLoss(x=self.plt_epoch,
                           y1=self.plt_g_loss,
                           y2=self.plt_df_loss,
                           y3=self.plt_cpb_loss,
                           y4=self.plt_dl_loss,
                           figName=self.model,
                           line1Name='g loss',
                           line2Name='df loss',
                           line3Name='cpb loss',
                           line4Name='dl loss',
                           savePath=self.ckptDir)

            utils.save2file(log1, self.ckptDir, self.model)

            self.writer.add_summary(summary, e)

            self.saver.save(self.sess, self.ckptDir + self.model + '-' + str(e))

            self.test_procedure(self.source_test_data, distribution_op=self.distribution_source, inputX=self.x_source,
                                inputY=self.y_source, mode='source')

            self.test_procedure(self.target_test_data, distribution_op=self.distribution_target, inputX=self.x_target,
                                inputY=self.y_target, mode='target')

            source_training_acc = 0.0
            g_loss = 0.0
            d_loss_feature = 0.0
            d_loss_label = 0.0
            cpb_loss = 0.0
