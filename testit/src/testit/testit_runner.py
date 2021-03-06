#!/usr/bin/python

# Software License Agreement (BSD License)
#
# Copyright (c) 2019 Gert Kanter.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# Author: Gert Kanter
import json
import re
import traceback
import yaml

import roslib
import rospy
import testit_common
import testit_optimizer
import sys
import actionlib
import message_converter
import testit_msgs.msg
import std_msgs.msg


def get_attribute(value, path):
    get_value = getattr if not isinstance(value, dict) else lambda v, a: v.get(a)
    for attribute in path.split('.'):
        value = get_value(value, attribute)
    return value


def get_all_keys_recursively(dict_):
    if not isinstance(dict_, dict):
        return []
    keys = list(dict_.keys())
    for key in dict_:
        keys += get_all_keys_recursively(dict_[key])
    return tuple(sorted(keys))


class TestItRunner:
    def __init__(self, log_filename, weights_filename, test, with_logger, selection_mode):
        try:
            log_data = testit_common.parse_json_stream_file(log_filename)
        except:
            rospy.logwarn("Could not open log file '%s'!" % log_filename)

        try:
            weights = testit_common.parse_yaml(weights_filename)
            weights = weights['weights']
        except:
            rospy.logwarn("Could not open weights file '%s'!" % weights_filename)

        try:
            logger_config_path = rospy.get_param('testit_logger/config')
            with open(logger_config_path, 'r') as file:
                self.logger_config = yaml.load(file)
            self.inputs = self.logger_config['configuration']['inputs']
        except:
            rospy.logwarn("Logger not started, necessary for topic type commands")

        self.publishers = {}  # '/channel': rospy.Publisher(...)
        self.action_clients = {}  # '/channel': rospy.SimpleActionclient(...)
        self.subscribers = {}
        self.imports = []
        self.optimizer = testit_optimizer.Optimizer(log_data, weights, test)
        # TODO support "srv" mode
        self.coverage_publisher = None
        self.with_logger = with_logger
        if not with_logger:
            self.coverage_publisher = rospy.Publisher("/testit/flush_coverage", std_msgs.msg.UInt32, queue_size=10)
        self.coverage_subscriber = rospy.Subscriber("/testit/flush_data", testit_msgs.msg.FlushData,
                                                    self.flush_subscriber)
        self.param_state = {}  # {(filename, line): probability), ...}
        self.selection_mode = selection_mode

    def flush_subscriber(self, data):
        # rospy.loginfo("received flush data")
        try:
            for file_coverage in data.coverage:
                for line in file_coverage.lines:
                    self.param_state[(file_coverage.filename, line)] = 1.0
        except Exception as e:
            rospy.logerr("Exception from flush subscriber: %s" % e)
        pass

    def find_state_hash(self, next_step, data):
        state_hash_dict = self.optimizer.state_hashes[next_step[0]][0]
        state_hash = None
        for state_hash in state_hash_dict:
            state = json.loads(state_hash_dict[state_hash]
                               .replace("u'", '"').replace("'", '"').replace("None", "null")
                               .replace("True", "true").replace("False", "false"))
            # Check if message structure is same
            if get_all_keys_recursively(state) == get_all_keys_recursively(data):
                break
        return state_hash

    def run(self):
        next_step = ["INIT", {}, 0]
        while True:
            # rospy.loginfo("param_state = %s" % self.param_state)
            rospy.logwarn("Current state value == %s" % self.optimizer.compute_parameter_state_value(self.param_state))
            next_step = self.optimizer.compute_step(10, next_step[0], self.param_state,
                                                    self.selection_mode)  # selection_mode=0 best, 1 = random, 2 = worst
            if next_step[0] is None:
                rospy.logerr("No next step!")
                break
            data = self.optimizer.state_hashes[next_step[0]][1]
            state_hash = self.find_state_hash(next_step, data)
            channel = self.optimizer.channel_hashes[state_hash]

            # rospy.loginfo("next_step == %s  data == %s  channel == %s" % (list(next_step), data, channel))
            if not self.command(data, channel):
                rospy.logerr("Unable to succeed with command!")
                break

    def command(self, data, channel):
        # rospy.loginfo("command(%s, %s)" % (data, channel))
        channel_type = channel.get('type', "")
        if channel_type not in self.imports:
            if self.do_import(channel_type):
                self.imports.append(channel_type)

        if channel_type.endswith("Action"):
            return self.handle_action(channel, channel_type, data)
        else:
            return self.handle_topic(channel, data)

    def handle_action(self, channel, channel_type, data):
        if self.action_clients.get(channel['identifier'], None) is None:
            # Register action server and client
            eval("self.action_clients.update({'" + channel['identifier'] + "': actionlib.SimpleActionClient(\"" +
                 channel['identifier'] + "\", " + channel_type + ")})", dict(globals().items() + [('self', self)]))
            rospy.loginfo("Waiting for '%s' action server..." % channel['identifier'])
            self.action_clients[channel['identifier']].wait_for_server()
        message_type = channel_type.replace("Action", "").replace(".msg", "").replace(".", "/") + "Goal"
        self.action_clients[channel['identifier']].send_goal(
            message_converter.convert_dictionary_to_ros_message(message_type, data))
        rospy.sleep(1.0)
        self.action_clients[channel['identifier']].wait_for_result()
        state = self.action_clients[channel['identifier']].get_state()
        if state != 3:
            rospy.sleep(1.0)
            return self.command(data, channel)
        else:
            if not self.with_logger:
                data = std_msgs.msg.UInt32(0)
                self.coverage_publisher.publish(data)
                rospy.sleep(0.5)
            return True

    def handle_topic(self, channel, data):
        try:
            identifier = channel['identifier']
            message_class = []
            message = message_converter.convert_dictionary_to_ros_message(
                channel['type'].replace(".msg", "").replace(".", "/"), data, message_class_return=message_class)

            if identifier not in self.publishers:
                self.publishers[identifier] = rospy.Publisher(identifier, message_class.pop(), queue_size=1)
                rospy.sleep(1)
            for input_config in self.inputs:
                if input_config['identifier'] == identifier:
                    response_config = input_config['feedback']

            publisher = self.publishers[identifier]
            publisher.publish(message)
            rospy.loginfo("Published msg:\n" + str(message))

            feedback_class = roslib.message.get_message_class(response_config['type'].replace(".msg", "").replace(".", "/"))
            response = rospy.wait_for_message(response_config['topic'], feedback_class)
            rospy.loginfo("Response:\n" + str(response))
            result = get_attribute(response, response_config['field'])
            success = response_config.get('success', result) == result or re.match(str(response_config['success']),
                                                                                   str(result)) is not None

            rospy.loginfo("\nResult %s \n, success %s \n" % (str(result), str(success)))
            if success:
                if not self.with_logger:
                    data = std_msgs.msg.UInt32(0)
                self.coverage_publisher.publish(data)
                rospy.sleep(0.5)
                return True
            else:
                return False
        except:
            rospy.logerr("Couldn't find feedback topic from logger config?")
            traceback.print_exc()
            return False

    def do_import(self, channel_type):
        import_string = ".".join(channel_type.split(".")[:-1])
        exec ("import " + import_string, globals())
        return True


if __name__ == "__main__":
    rospy.init_node('testit_runner')
    filename = rospy.get_param('~filename', None)
    if filename is None:
        rospy.logerr("filename not specified!")
        sys.exit(-1)
    test = rospy.get_param('~test', None)
    if test is None:
        rospy.logerr("test scenario not specified!")
        sys.exit(-1)
    weights = rospy.get_param('~weights', None)
    if weights is None:
        rospy.logerr("weights not specified!")
        sys.exit(-1)
    with_logger = rospy.get_param('~with_logger', False)
    selection_mode = rospy.get_param('~selection_mode', 0)
    testit_runner = TestItRunner(filename, weights, test, with_logger, selection_mode)
    rospy.loginfo("TestIt runner initialized...")
    testit_runner.run()
