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

import rospy
import testit_common
import testit_optimizer
import sys
import actionlib
import message_converter

class TestItRunner:
    def __init__(self, log_filename, weights_filename, test):
        try:
            log_data = testit_common.parse_json_stream_file(log_filename)
        except:
            rospy.logwarn("Could not open log file '%s'!" % log_filename)
        try:
            weights = testit_common.parse_yaml(weights_filename)
            weights = weights['weights']
        except:
            rospy.logwarn("Could not open weights file '%s'!" % weights_filename)
        self.publishers = {} # '/channel': rospy.Publisher(...)
        self.action_clients = {} # '/channel': rospy.SimpleActionclient(...)
        self.imports = []
        self.optimizer = testit_optimizer.Optimizer(log_data, weights, test)

        next_step = ["INIT", {}, 0]
        while True:
            next_step = self.optimizer.compute_step(2, next_step[0], next_step[1])
            data = self.optimizer.state_hashes[next_step[0]][1]
            channel = self.optimizer.channel_hashes[self.optimizer.state_hashes[next_step[0]][0].keys()[0]]

            rospy.loginfo("next_step == %s  data == %s  channel == %s" % (list(next_step), data, channel))
            channel_type = channel.get('type', "")
            if channel_type not in self.imports:
                if self.do_import(channel_type):
                    self.imports.append(channel_type)

            if channel_type.endswith("Action"):
                if self.action_clients.get(channel['identifier'], None) is None:
                    # Register action server and client
                    eval("self.action_clients.update({'" + channel['identifier'] + "': actionlib.SimpleActionClient(\"" + channel['identifier'] + "\", " + channel_type + ")})", dict(globals().items() + [('self', self)]))
                    rospy.loginfo("Waiting for '%s' action server..." % channel['identifier'])
                    self.action_clients[channel['identifier']].wait_for_server()
                # Send command via client
                #self.goal = []
                #eval("self.goal.append(" + channel_type.replace("Action", "") + "Goal())", dict(globals().items() + [('self', self)]))
                message_type = channel_type.replace("Action", "").replace(".msg", "").replace(".", "/") + "Goal"
                rospy.loginfo(message_type)
                #self.goal[0].goal = 
                #rospy.loginfo(self.goal)
                self.action_clients[channel['identifier']].send_goal(message_converter.convert_dictionary_to_ros_message(message_type, data))
                self.action_clients[channel['identifier']].wait_for_result()

    def do_import(self, channel_type):
        import_string = ".".join(channel_type.split(".")[:-1])
        exec("import " + import_string, globals())
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
    testit_runner = TestItRunner(filename, weights, test)
    rospy.loginfo("TestIt runner started...")


    rospy.spin()
    rospy.loginfo("Shut down everything!")
