#!/usr/bin/env python

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
import sys
import actionlib

class TestItLogger(object):
    def __init__(self):
        self.initialize()
        self.register_services_and_subscribe()

    def initialize(self):
        self.load_config_from_file()
        self.configuration = rospy.get_param('testit/configuration', None)
        self.action_servers = []
        if self.configuration is None:
            rospy.logerr("Logger configuration not defined!")
            sys.exit(-1)
        self.log_file = rospy.get_param('~log', None)
        if self.log_file is None:
            rospy.logerr("Log file not defined!")
            sys.exit(-1)

    def register_services_and_subscribe(self):
        """
        Subscribe to topics - both input and output
        """
        rospy.loginfo("Subscribing to topics...")
        if self.configuration.get('inputs', None) is not None:
            for channel in map(lambda x: (x, 'input'), self.configuration.get('inputs', [])) + map(lambda x: (x, 'output'), self.configuration.get('outputs', [])):
                identifier = channel[0].get('identifier', "")
                rospy.loginfo("Processing channel: %s" % str(channel))
                if identifier != "":
                    channel_type = channel[0].get('type', "")
                    if channel_type != "":
                        rospy.loginfo("%s" % channel[0])
                        self.do_import(channel_type)
                        proxy = channel[0].get('proxy', "")
                        if proxy == "":
                            eval("rospy.Subscriber(\"" + identifier + "\", " + channel_type + ", self.topic_callback, callback_args=(\"" + channel[1] + "\", \"" + identifier + "\"))")
                            rospy.loginfo("Subscribed to %s" % identifier)
                        else:
                            if "Action" in channel_type:
                                # Register actionserver
                                eval("self.action_servers.append(actionlib.SimpleActionServer(\"" + proxy + "\", " + channel_type + ", lambda x: self.action_handler(x, \"" + identifier + "\", \"" + proxy + "\")))", dict(globals().items() + [('self', self)]))
                                rospy.loginfo("Registered proxy actionserver %s" % proxy)
                            else:
                                # Register service
                                eval("rospy.Service(\"" + proxy + "\", " + channel_type + ", lambda x: self.service_handler(x, \"" + identifier + "\"))", dict(globals().items() + [('self', self)]))
                                rospy.loginfo("Registered proxy service %s" % identifier)

    def do_import(self, channel_type):
        import_string = ".".join(channel_type.split(".")[:-1])
        rospy.loginfo("Importing '%s'" % import_string)
        exec("import " + import_string, globals())

    def topic_callback(self, data, args):
        rospy.logwarn(args)
        rospy.logerr(data)

    def load_config_from_file(self):
        filename = rospy.get_param('~config')
        rospy.loginfo("Loading configuration from " + filename + "...")
        testit_common.load_config_to_rosparam(testit_common.parse_yaml(filename))

    def add_entry(self, data):
        testit_common.append_to_json_file(data, self.log_file)

    def service_handler(self, req, args):
        rospy.loginfo("service_handler")
        rospy.logerr(self.configuration)
        rospy.logerr(args)
        rospy.logwarn(type(req))
        return ()

    def get_action_server(self, identifier):
        for action_server in self.action_servers:
            if action_server.action_server.ns == identifier:
                return action_server
        return None

    def action_handler(self, goal, identifier, proxy):
        rospy.loginfo("action_handler")
        rospy.logerr(self.configuration)
        rospy.logerr(self.action_servers)
        rospy.logerr(identifier)
        rospy.logerr(proxy)
        rospy.logwarn(type(goal))
        action_server = self.get_action_server(proxy)
        if action_server is not None:
            action_server.set_succeeded()
            rospy.loginfo("set succeeded")

if __name__ == "__main__":
    rospy.init_node('testit_logger')
    testit_logger = TestItLogger()
    rospy.loginfo("TestIt logger started...")
    rospy.spin()
    rospy.loginfo("Shut down everything!")

