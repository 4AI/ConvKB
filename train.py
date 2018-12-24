import tensorflow as tf
import numpy as np

np.random.seed(1234)
import os
import time
import datetime
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from builddata import *
from model import ConvKB

# Parameters
# ==================================================
parser = ArgumentParser("ConvKB", formatter_class=ArgumentDefaultsHelpFormatter, conflict_handler='resolve')

parser.add_argument("--data", default="./data/", help="Data sources.")
parser.add_argument("--run_folder", default="../", help="Data sources.")
parser.add_argument("--name", default="WN18RR", help="Name of the dataset.")

parser.add_argument("--embedding_dim", default=50, type=int, help="Dimensionality of character embedding")
parser.add_argument("--filter_sizes", default="1", help="Comma-separated filter sizes")
parser.add_argument("--num_filters", default=500, type=int, help="Number of filters per filter size")
parser.add_argument("--dropout_keep_prob", default=1.0, type=float, help="Dropout keep probability")
parser.add_argument("--l2_reg_lambda", default=0.001, type=float, help="L2 regularization lambda")
parser.add_argument("--learning_rate", default=0.0001, type=float, help="Learning rate")
parser.add_argument("--is_trainable", default=True, type=bool, help="")
parser.add_argument("--batch_size", default=128, type=int, help="Batch Size")
parser.add_argument("--neg_ratio", default=1.0, type=float, help="Number of negative triples generated by positive")
parser.add_argument("--use_pretrained", default=True, type=bool, help="Using the pretrained embeddings")
parser.add_argument("--num_epochs", default=201, type=int, help="Number of training epochs")
parser.add_argument("--saveStep", default=200, type=int, help="")
parser.add_argument("--allow_soft_placement", default=True, type=bool, help="Allow device soft device placement")
parser.add_argument("--log_device_placement", default=False, type=bool, help="Log placement of ops on devices")
parser.add_argument("--model_name", default='wn18rr', help="")
parser.add_argument("--useConstantInit", action='store_true')

parser.add_argument("--model_index", default='200', help="")
parser.add_argument("--num_splits", default=8, type=int, help="Split the validation set into 8 parts for a faster evaluation")
parser.add_argument("--testIdx", default=1, type=int, help="From 0 to 7. Index of one of 8 parts")
parser.add_argument("--decode", action='store_false')

args = parser.parse_args()
print(args)

# Load data
print("Loading data...")

train, valid, test, words_indexes, indexes_words, \
headTailSelector, entity2id, id2entity, relation2id, id2relation = build_data(path=args.data, name=args.name)
data_size = len(train)
train_batch = Batch_Loader(train, words_indexes, indexes_words, headTailSelector, \
                           entity2id, id2entity, relation2id, id2relation, batch_size=args.batch_size,
                           neg_ratio=args.neg_ratio)

entity_array = np.array(list(train_batch.indexes_ents.keys()))

lstEmbed = []
if args.use_pretrained == True:
    print("Using pre-trained model.")
    lstEmbed = np.empty([len(words_indexes), args.embedding_dim]).astype(np.float32)
    initEnt, initRel = init_norm_Vector(args.data + args.name + '/relation2vec' + str(args.embedding_dim) + '.init',
                                        args.data + args.name + '/entity2vec' + str(args.embedding_dim) + '.init',
                                        args.embedding_dim)
    for _word in words_indexes:
        if _word in relation2id:
            index = relation2id[_word]
            _ind = words_indexes[_word]
            lstEmbed[_ind] = initRel[index]
        elif _word in entity2id:
            index = entity2id[_word]
            _ind = words_indexes[_word]
            lstEmbed[_ind] = initEnt[index]
        else:
            print('*****************Error********************!')
            break
    lstEmbed = np.array(lstEmbed, dtype=np.float32)

assert len(words_indexes) % (len(entity2id) + len(relation2id)) == 0

print("Loading data... finished!")

x_valid = np.array(list(valid.keys())).astype(np.int32)
y_valid = np.array(list(valid.values())).astype(np.float32)

x_test = np.array(list(test.keys())).astype(np.int32)
y_test = np.array(list(test.values())).astype(np.float32)

# Training
# ==================================================
with tf.Graph().as_default():
    tf.set_random_seed(1234)
    session_conf = tf.ConfigProto(allow_soft_placement=args.allow_soft_placement,
                                  log_device_placement=args.log_device_placement)
    session_conf.gpu_options.allow_growth = True
    sess = tf.Session(config=session_conf)
    with sess.as_default():
        global_step = tf.Variable(0, name="global_step", trainable=False)
        cnn = ConvKB(
            sequence_length=x_valid.shape[1],  # 3
            num_classes=y_valid.shape[1],  # 1
            pre_trained=lstEmbed,
            embedding_size=args.embedding_dim,
            filter_sizes=list(map(int, args.filter_sizes.split(","))),
            num_filters=args.num_filters,
            vocab_size=len(words_indexes),
            l2_reg_lambda=args.l2_reg_lambda,
            is_trainable=args.is_trainable,
            useConstantInit=args.useConstantInit)

        optimizer = tf.train.AdamOptimizer(learning_rate=args.learning_rate)
        # optimizer = tf.train.RMSPropOptimizer(learning_rate=args.learning_rate)
        # optimizer = tf.train.GradientDescentOptimizer(learning_rate=args.learning_rate)
        grads_and_vars = optimizer.compute_gradients(cnn.loss)
        train_op = optimizer.apply_gradients(grads_and_vars, global_step=global_step)

        out_dir = os.path.abspath(os.path.join(args.run_folder, "runs", args.model_name))
        print("Writing to {}\n".format(out_dir))

        # Checkpoint directory. Tensorflow assumes this directory already exists so we need to create it
        checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
        checkpoint_prefix = os.path.join(checkpoint_dir, "model")
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)

        # Initialize all variables
        sess.run(tf.global_variables_initializer())

        def train_step(x_batch, y_batch):
            """
            A single training step
            """
            feed_dict = {
                cnn.input_x: x_batch,
                cnn.input_y: y_batch,
                cnn.dropout_keep_prob: args.dropout_keep_prob,
            }
            _, step, loss = sess.run([train_op, global_step, cnn.loss], feed_dict)

        num_batches_per_epoch = int((data_size - 1) / args.batch_size) + 1
        for epoch in range(args.num_epochs):
            for batch_num in range(num_batches_per_epoch):
                x_batch, y_batch = train_batch()
                train_step(x_batch, y_batch)
                current_step = tf.train.global_step(sess, global_step)

            if epoch > 0:
                if epoch % args.saveStep == 0:
                    path = cnn.saver.save(sess, checkpoint_prefix, global_step=epoch)
                    print("Saved model checkpoint to {}\n".format(path))
