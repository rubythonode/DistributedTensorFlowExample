"""
Distributed Tensorflow example
The original code was in @ischlag, but the distributed architecture is quite
different.
The code runs on TF 1.1. 
Trains a simple sigmoid neural network on mnist for 20 epochs on three machines using one parameter server. 
The code requires 'tmux'.
The code runs on the local server only.

Run like this: 
$ bash run.sh

Then, by using ctrl+b+(window number, e.g., 0, 1, 2, 3 in NumPad), 
you can change the terminal.

"""
from __future__ import print_function
import tensorflow as tf
import sys
import time

# Class to define network.
class WorkerThread(object):
    def __init__(self, job_name, task_index, server):
        self.job_name = job_name
        self.task_index = task_index

        # For shared parameters, including global step.
        global_device = '/job:{}/task:{}/cpu:0'.format(job_name, task_index)

        # For local computations,
        """
        Note
        ----
        The gradient computation occurs at each local_devices.
        Also, "CUDA_VISIBLE_DEVICES" for each worker process allocates single
        gpu.
        Thus, I set '/gpu:0'.
        """
        local_device = '/job:{}/task:{}/gpu:0'.format(job_name, task_index)

        with tf.device(tf.train.replica_device_setter(1,
            worker_device=global_device)):

            with tf.variable_scope('global'):
                self.build_net()
                self.global_step = tf.train.get_or_create_global_step()

        with tf.device(local_device):
            with tf.variable_scope('local'):
                self.build_net()
                self.build_loss()
                self.build_train_op()
                self.build_summary_op()
                self.build_sync_op()

        self.build_init_op()

        self.saver = FastSaver(self.global_vars)


    def build_net(self):
        self.x = tf.placeholder(tf.float32, [None, 784])

        def _net(inputs):
            net = tf.layers.dense(inputs, 100, activation=tf.nn.sigmoid,
                    kernel_initializer=tf.random_normal_initializer())
            logits = tf.layers.dense(net, 10,
                    kernel_initializer=tf.random_normal_initializer())
            net = tf.nn.softmax(logits)
            return net, logits

        self.net, self.logits = _net(self.x) 

    def build_loss(self):
        self.y = tf.placeholder(tf.float32, [None, 10])

        def _loss(labels, logits):
            cross_entropy = tf.nn.softmax_cross_entropy_with_logits(
                    labels=labels, logits=logits)
            
            return tf.reduce_mean(cross_entropy)
        
        self.loss = _loss(self.y, self.logits)

    def build_train_op(self):
        optimizer = tf.train.GradientDescentOptimizer(FLAGS.learning_rate)
        local_vars = self.local_vars =\
                tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES,
                        scope='local')
        gvs = optimizer.compute_gradients(self.loss, var_list=local_vars)
        
        global_vars = self.global_vars =\
                tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES,
                        scope='global')
        global_gvs = []
        for global_var, gv in zip(global_vars, gvs):
            global_gvs.append((gv[0], global_var))
        
        # The code below might not work.
        self.train_op = optimizer.apply_gradients(global_gvs)
 
    def build_summary_op(self):
        with tf.name_scope('accuracy'):
            correct_prediction = tf.equal(tf.argmax(self.net, 1), tf.argmax(self.y, 1))
            accuracy = self.accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))
        
        tf.summary.scalar('loss', self.loss)
        tf.summary.scalar('accuracy', accuracy)

        self.summary_op = tf.summary.merge_all()
        self.summary_writer = tf.summary.FileWriter(FLAGS.logdir + '_%d' % self.task_index)

    def build_init_op(self):
        self.init_op = tf.variables_initializer(self.global_vars)
        self.init_all_op = tf.global_variables_initializer()
        all_global_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES,
                scope='global')
        self.global_init_op = tf.variables_initializer(all_global_vars)
        self.local_init_op = tf.variables_initializer(self.local_vars)

    def init_fn(self, ses):
        ses.run(self.init_all_op)

    def build_sync_op(self):
        self.sync_op = tf.group(*[v1.assign(v2)\
                for v1, v2 in zip(self.local_vars, self.global_vars)])

    def learn(self):

        # Define Supervisor.
        sv = tf.train.Supervisor(is_chief=(self.task_index==0),
                                 logdir=FLAGS.logdir,
                                 saver=None,
                                 summary_op=None,
                                 summary_writer=self.summary_writer,
                                 ready_op=tf.report_uninitialized_variables(self.global_vars),
                                 global_step=self.global_step,
                                 save_model_secs=30,
                                 save_summaries_secs=30,
                                 init_op=self.global_init_op,
                                 local_init_op=self.local_init_op)
#                                 init_op=self.init_op,
#                                 init_fn=self.init_fn)

        with sv.managed_session(server.target) as sess, sess.as_default():

            begin_time = time.time()
            frequency = 100
            # perform training cycles
            start_time = time.time()

            epoch = 0
            while not sv.should_stop() and epoch < FLAGS.training_epochs:
                # number of batches in one epoch
                batch_count = int(mnist.train.num_examples/FLAGS.batch_size)           
                count = 0
                for i in range(batch_count):
                    batch_x, batch_y = mnist.train.next_batch(FLAGS.batch_size)           		
                    
                    # perform the operations we defined earlier on batch
                    _, cost, summary, step = sess.run(
                            [self.train_op, self.loss, self.summary_op, self.global_step], 
                            feed_dict={self.x: batch_x, self.y: batch_y})
                    self.summary_writer.add_summary(summary, step)
                    
                    count += 1
                    if count % frequency == 0 or i+1 == batch_count:
                        elapsed_time = time.time() - start_time
                        start_time = time.time()
                        print("Step: %d," % (step+1), 
                                " Epoch: %2d," % (epoch+1), 
                                " Batch: %3d of %3d," % (i+1, batch_count), 
                                " Cost: %.4f," % cost, 
                                " AvgTime: %3.2fms" % float(elapsed_time*1000/frequency))
                        count = 0
                
                epoch += 1          
            
            print("Test-Accuracy: %2.2f" % sess.run(self.accuracy, 
                feed_dict={self.x: mnist.test.images, self.y: mnist.test.labels}))
            print("Total Time: %3.2fs" % float(time.time() - begin_time))
            print("Final Cost: %.4f" % cost)
            
            print("done")
        

def cluster_spec(num_workers, num_ps):
    cluster = {}
    port = 12222

    all_ps = []
    host = '127.0.0.1'
    for _ in range(num_ps):
        all_ps.append('{}:{}'.format(host, port))
        port += 1
    cluster['ps'] = all_ps

    all_workers = []
    for _ in range(num_workers):
        all_workers.append('{}:{}'.format(host, port))
        port += 1
    cluster['worker'] = all_workers
    return cluster

class FastSaver(tf.train.Saver):
    def save(self, sess, save_path, global_step=None, latest_filename=None,
             meta_graph_suffix='meta', write_meta_graph=True):
        super(FastSaver, self).save(ses, save_path, global_step,
                latest_filename, meta_graph_suffix, False)
                    




# Define flags.
flags = tf.app.flags
flags.DEFINE_string('job_name', 'ps', "Either 'ps' or 'worker'")
flags.DEFINE_integer('task_index', 0, "Index of task within the job")
flags.DEFINE_integer('batch_size', 100, "Batch size")
flags.DEFINE_float('learning_rate', 0.0005, "Learning rate")
flags.DEFINE_integer('training_epochs', 20, "Training epochs")
flags.DEFINE_string('logdir', './tmp/mnist/1', "Log directory")
FLAGS = flags.FLAGS

# Load MNIST dataset.
from tensorflow.examples.tutorials.mnist import input_data
mnist = input_data.read_data_sets('MNIST_data', one_hot=True)

# cluster specification
spec = cluster_spec(2, 1)
cluster = tf.train.ClusterSpec(spec).as_cluster_def()

# start a server for a specific task_index
gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.4)

if FLAGS.job_name == 'ps':
    config = tf.ConfigProto(device_filters=['/job:ps'])
    server = tf.train.Server(cluster, job_name='ps',
            task_index=FLAGS.task_index, config=config)
                
    while True:
        time.sleep(1000)

elif FLAGS.job_name == 'worker':
    config = tf.ConfigProto(gpu_options=gpu_options,
                            intra_op_parallelism_threads=1,
                            inter_op_parallelism_threads=2)
    server = tf.train.Server(cluster, job_name='worker',
            task_index=FLAGS.task_index, config=config)
    worker = WorkerThread(FLAGS.job_name, FLAGS.task_index, server)
    worker.learn()
        
   

