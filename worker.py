#!/usr/bin/env python
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import sys
import time

import tensorflow as tf
import coref_model as cm
import util

if __name__ == "__main__":
  args = util.get_args()
  config = util.initialize_from_env(args.experiment, args.logdir)
  task_index = int(os.environ["TASK"])

  report_frequency = config["report_frequency"]
  cluster_config = util.get_cluster_config()

  util.set_gpus(cluster_config["gpus"][task_index])

  cluster = tf.train.ClusterSpec(cluster_config["addresses"])
  server = tf.train.Server(cluster,
                           job_name="worker",
                           task_index=task_index)

  # Assigns ops to the local worker by default.
  with tf.device(tf.train.replica_device_setter(worker_device="/job:worker/task:%d" % task_index, cluster=cluster)):
    model = cm.CorefModel(config)
    saver = tf.train.Saver()
    init_op = tf.global_variables_initializer()

  log_dir = config["log_dir"]
  writer = tf.summary.FileWriter(os.path.join(log_dir, "w{}".format(task_index)), flush_secs=20)

  is_chief = (task_index == 0)

  # Create a "supervisor", which oversees the training process.
  sv = tf.train.Supervisor(is_chief=is_chief,
                           logdir=log_dir,
                           init_op=init_op,
                           saver=saver,
                           global_step=model.global_step,
                           save_model_secs=120)

  # The supervisor takes care of session initialization, restoring from
  # a checkpoint, and closing when done or an error occurs.
  with sv.managed_session(server.target) as session:
    model.start_enqueue_thread(session)
    accumulated_loss = 0.0
    local_steps = 0
    prev_report_global_steps = session.run(model.global_step)
    prev_report_time = time.time()
    while not sv.should_stop():
      tf_loss, tf_global_step, _ = session.run([model.loss, model.global_step, model.train_op])
      accumulated_loss += tf_loss
      local_steps += 1

      if local_steps == report_frequency:
        total_time = time.time() - prev_report_time
        steps_per_second = (tf_global_step - prev_report_global_steps) / total_time

        average_loss = accumulated_loss / report_frequency
        print("[{}] loss={:.2f}, steps/s={:.2f}".format(tf_global_step, average_loss, steps_per_second))
        accumulated_loss = 0.0
        local_steps = 0
        prev_report_global_steps = tf_global_step
        prev_report_time = time.time()
        writer.add_summary(util.make_summary({
          "loss": average_loss,
          "Steps per second": steps_per_second
        }), global_step=tf_global_step)

  # Ask for all the services to stop.
  sv.stop()
