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
import testit.srv
import rosparam
import rospkg
import testit_common
import threading
import time
import sys
import re
import subprocess
import testit.junit
import cStringIO
import xml.etree.ElementTree

class TestItDaemon:
    def __init__(self):
        rospy.Service('testit/bringup', testit.srv.Command, self.handle_bringup)
        rospy.Service('testit/teardown', testit.srv.Command, self.handle_teardown)
        rospy.Service('testit/status', testit.srv.Command, self.handle_status)
        rospy.Service('testit/test', testit.srv.Command, self.handle_test)
        rospy.Service('testit/results', testit.srv.Command, self.handle_results)
        rospy.Service('testit/bag', testit.srv.Command, self.handle_bag)
        rospy.Service('testit/coverage', testit.srv.Command, self.handle_coverage)
        rospy.Service('testit/uppaal/annotate/coverage', testit.srv.Command, self.handle_uppaal_annotate_coverage)
        self.initialize()

    def initialize(self):
        self.load_config_from_file()
        self.threads = {}
        self.test_threads = {}
        self.testing = False
        self.call_result = {}
        self.configuration = rospy.get_param('testit/configuration', None)
        if self.configuration is None:
            rospy.logerror("No configuration defaults defined in configuration!")
            sys.exit(-1)
        self.tests = self.set_defaults(self.rosparam_list_to_dict(rospy.get_param('testit/tests', None), 'tag'), self.configuration)
        if self.tests is None:
            rospy.logerror("No tests defined in configuration!")
            sys.exit(-1)
        self.pipelines = self.rosparam_list_to_dict(rospy.get_param('testit/pipelines', None), 'tag')
        if self.pipelines is None:
            rospy.logerror("No pipelines defined in configuration!")
            sys.exit(-1)
        self.pipelines = self.substitute_config_values(
                           self.set_defaults(self.pipelines, 
                                             self.configuration))

    def substitute_config_values(self, params):
        for param in params:
            for key in params[param]:
                m = re.findall('(\[\[.*?\]\])', str(params[param][key]))
                if m is not None:
                    for replacement in m:
                        params[param][key] = params[param][key].replace(replacement, params[param][replacement[2:-2]], 1)
        return params


    def set_defaults(self, params, defaults):
        for param in params:
            for key in params[param]:
                try:
                    if params[param][key] == '' and defaults[key] != '':
                        # set default
                        params[param][key] = defaults[key]
                except:
                    pass
        return params

    def rosparam_list_to_dict(self, param, key):
        if param is None:
            return
        return_value = {}
        for item in param:
            return_value[item[key]] = item
        return return_value

    def load_config_from_file(self):
        filename = rospy.get_param('~config')
        rospy.loginfo("Loading configuration from " + filename + "...")
        testit_common.load_config_to_rosparam(testit_common.parse_yaml(filename))

    def execution_sleep(self, tag, prefix, instance):
        start_time = rospy.Time.now()
        while self.pipelines[tag]['state'] != "TEARDOWN" and (self.pipelines[tag][prefix + instance + 'Timeout'] == 0 or (rospy.Time.now() - start_time).to_sec() < self.pipelines[tag][prefix + instance + 'Timeout']):
            if self.pipelines[tag][prefix + instance + 'FinishTrigger'] != '-':
                # TODO using timeout + trigger
                pass
            time.sleep(1.0)
        rospy.loginfo('[%s] Done!' % tag)

    def instance_execution(self, tag, prefix, instance, set_result):
        rospy.loginfo('[%s] Executing %s %s...' % (tag, prefix, instance))
        if subprocess.call(self.pipelines[tag][prefix + instance], shell=True) == 0:
            rospy.loginfo('[%s] Done!' % tag)
            rospy.loginfo('[%s] Waiting for delay duration (%s)...' % (tag, self.pipelines[tag][prefix + instance + 'Delay']))
            time.sleep(self.pipelines[tag][prefix + instance + 'Delay'])
            rospy.loginfo('[%s] Waiting for the %s to finish...' % (tag, prefix))
            self.execution_sleep(tag, prefix, instance)
            if self.pipelines[tag].get('state', "OFFLINE") != "TEARDOWN":
                if set_result:
                    self.threads[tag]['result'] = True
                return True
            else:
                rospy.logerr("Pipeline in TEARDOWN state!")
                self.threads[tag]['result'] = False
                return False
        else:
            rospy.logerr("[%s] Failed to execute %s!" % (tag, instance))
            self.threads[tag]['result'] = False
            return False

    def thread_worker(self, tag, prefix, post_states):
        rospy.logdebug('[%s] thread_worker started!' % tag)
        if self.instance_execution(tag, prefix, "SUT", False):
            if self.instance_execution(tag, prefix, "TestIt", True):
                self.pipelines[tag]['state'] = post_states['True']
                return True
        self.pipelines[tag]['state'] = post_states['False']

    def multithreaded_command(self, verb, req, prefix, pre_state, post_states, extra_commands=[]):
        rospy.logdebug(verb + " requested")
        if req.args == "":
            rospy.loginfo(verb + " all pipelines...")
        else:
            rospy.loginfo(verb + "ing " + req.args + "...")
        for pipe in rospy.get_param('testit/pipelines', []):
            if req.args == '' or req.args == pipe['tag']:
                rospy.loginfo("[%s] Setting state to %s" % (pipe['tag'], pre_state))
                self.pipelines[pipe['tag']]['state'] = pre_state
                if prefix == "teardown":
                    # run stop just in case
                    self.execute_system(pipe['tag'], 'SUT', 'stop')
                    self.execute_system(pipe['tag'], 'TestIt', 'stop')
                # Run extra_commands before executing the main command
                for command in extra_commands:
                    command(pipe['tag'])
                rospy.loginfo(pipe['tag'] + " " + verb.lower() + "ing...")
                thread = threading.Thread(target=self.thread_worker, args=(pipe['tag'], prefix, post_states))
                self.threads[pipe['tag']] = {'thread': thread, 'result': None}
                thread.start()
        result = True
        message = ""
        while len(self.threads) > 0:
            for thread in self.threads:
                if not self.threads[thread]['result'] is None:
                    msg = '%s finished with %r' % (thread, self.threads[thread]['result'])
                    rospy.loginfo(msg)
                    if not self.threads[thread]['result']:
                        # Report if experienced a failure...
                        message += msg + "\n"
                        self.pipelines[thread][prefix] = False
                        result = False
                    else:
                        # Success!
                        self.pipelines[thread][prefix] = True
                    del self.threads[thread]
                    break
            time.sleep(1.0)
            rospy.loginfo_throttle(15.0, '...')
        return (result, message)

    def remove_bags(self, tag):
        rospy.loginfo("removing bags from tag = %s" % tag)

    def handle_bringup(self, req):
        result = self.multithreaded_command("Start", req, "bringup", "BRINGUP", {'True': "READY", 'False': "FAILED"})
        return testit.srv.CommandResponse(result[0], result[1])

    def handle_teardown(self, req):
        self.testing = False
        result = self.multithreaded_command("Stop", req, "teardown", "TEARDOWN", {'True': "OFFLINE", 'False': "OFFLINE"}, extra_commands=[self.remove_bags])
        return testit.srv.CommandResponse(result[0], result[1])

    def handle_status(self, req):
        rospy.logdebug("Status requested")
        message = ""
        result = True
        try:
            for pipeline in self.pipelines: # dict
                message += "[%s] %s\n" % (self.pipelines[pipeline]['tag'], self.pipelines[pipeline].get('state', "OFFLINE"))
        except:
            result = False
        return testit.srv.CommandResponse(result, message)

    def acquire_pipeline(self, tag):
        """
        Return:
        pipeline tag
        """
        rospy.loginfo("Acquiring pipeline for test \'%s\'" % tag)
        while True:
            for pipeline in self.pipelines:
                if self.pipelines[pipeline].get('state', "OFFLINE") == "READY":
                    self.pipelines[pipeline]['state'] = "BUSY"
                    return pipeline
            time.sleep(0.5)
            rospy.logwarn_throttle(30.0, 'Test \'%s\' waiting for a free pipeline...' % tag)

    def execute_system(self, pipeline, system, mode):
        """
        blocking
        Returns:
        true -- if successful, false otherwise
        """
        rospy.loginfo("[%s] Executing %s to %s..." % (pipeline, system, mode))
        rospy.loginfo("[%s] Executing \"%s\"" % (pipeline, self.pipelines[pipeline][mode + system]))
        if subprocess.call(self.pipelines[pipeline][mode + system], shell=True) == 0:
            rospy.loginfo('[%s] Waiting for delay duration (%s)...' % (pipeline, self.pipelines[pipeline][mode + system + 'Delay']))
            time.sleep(self.pipelines[pipeline][mode + system + 'Delay'])
            start_time = rospy.Time.now()
            while self.pipelines[pipeline]['state'] not in ["TEARDOWN", "FAILED", "OFFLINE"] and (self.pipelines[pipeline][mode + system + 'Timeout'] == 0 or (rospy.Time.now() - start_time).to_sec() < self.pipelines[pipeline][mode + system + 'Timeout']):
                if self.pipelines[pipeline][mode + system + 'FinishTrigger'] != '-':
                    # TODO using timeout + trigger
                    pass
                rospy.loginfo_throttle(15.0, '[%s] (%s) ..' % (pipeline, mode))
                time.sleep(1.0) 
            rospy.loginfo('[%s] Execution done!' % pipeline)
            if self.pipelines[pipeline]['state'] not in ["TEARDOWN", "FAILED", "OFFLINE"]:
                return True
        else:
            rospy.logerr('[%s] Execution failed!' % pipeline)
        return False

    def thread_call(self, tag, command):
        self.call_result[tag] = -1 # -1 means timeout
        self.call_result[tag] = subprocess.call(command, shell=True)

    def execute_in_testit_container(self, pipeline, test):
        """
        Returns:
        True if test successful, False otherwise
        """
        #TODO support ssh wrapping (currently only runs on localhost)
        # launch test in TestIt docker in new thread (if oracle specified, run in detached mode)
        if self.configuration.get('bagEnabled', False):
            rospy.loginfo("[%s] Start rosbag recording..." % pipeline)
            bag_result = subprocess.call( "docker exec -d " + self.pipelines[pipeline]['testItHost'] + " /bin/bash -c \'source /opt/ros/$ROS_VERSION/setup.bash && cd " + str(self.tests[test]['source']) + " && rosbag record -a --split --max-splits=" + str(self.tests[test]['bagMaxSplits']) + " --duration=" + str(self.tests[test]['bagDuration']) + " -O testit __name:=testit_rosbag_recorder\'", shell=True)
            rospy.loginfo("[%s] rosbag record returned %s" % (pipeline, bag_result))
        detached = ""
        if self.tests[test]['oracle'] != "":
            # run in detached
            detached = " -d "
        rospy.loginfo("[%s] Launching test \'%s\'" % (pipeline, test))
        if self.tests[test]['verbose']:
          rospy.loginfo("[%s] launch parameter is \'%s\'" % (pipeline, self.tests[test]['launch']))
        thread = threading.Thread(target=self.thread_call, args=('launch', "docker exec " + detached + self.pipelines[pipeline]['testItHost'] + " /bin/bash -c \'source /catkin_ws/devel/setup.bash && " + self.tests[test]['launch'] + "\'"))
        start_time = rospy.Time.now()
        thread.start()
        thread.join(self.tests[test]['timeout'])
        return_value = False
        if self.call_result['launch'] == 0:
            # command returned success
            if detached == "":
                # test success, because we didn't run in detached
                rospy.loginfo("[%s] TEST PASS!" % pipeline)
                return_value = True
            else:
                # running detached, run oracle to assess test pass/fail
                # execute oracle in TestIt docker
                rospy.loginfo("[%s] Executing oracle..." % pipeline)
                thread = threading.Thread(target=self.thread_call, args=('oracle', "docker exec " + self.pipelines[pipeline]['testItHost'] + " /bin/bash -c \'source /catkin_ws/devel/setup.bash && " + self.tests[test]['oracle'] + "\'"))
                thread.start()
                thread.join(max(0.1, self.tests[test]['timeout'] - (rospy.Time.now() - start_time).to_sec()))
                if self.call_result['oracle'] == 0:
                    # oracle reports test pass
                    rospy.loginfo("[%s] TEST PASS!" % pipeline)
                    return_value = True
                elif self.call_result['oracle'] == -1:
                    rospy.logwarn("[%s] TEST TIMEOUT (%s)!" % (pipeline, self.tests[test]['timeoutVerdict']))
                    if self.tests[test]['timeoutVerdict']:
                        return_value = True
                else:
                    # oracle reports test failed
                    rospy.logerr("[%s] TEST FAIL!" % pipeline)
                    return_value = True
        elif self.call_result['launch'] == -1:
            rospy.logwarn("[%s] TEST TIMEOUT (%s)!" % (pipeline, self.tests[test]['timeoutVerdict']))
            if self.tests[test]['timeoutVerdict']:
                return_value = True
        else:
            rospy.logerr("[%s] Test FAIL!" % pipeline)

        if return_value and self.configuration.get('bagEnabled', False):
            rospy.loginfo("[%s] Stop rosbag recording..." % pipeline)
            subprocess.call( "docker exec " + self.pipelines[pipeline]['testItHost'] + " /bin/bash -c \'source /catkin_ws/devel/setup.bash && rosnode kill /testit_rosbag_recorder && sleep 4\'", shell=True)
        return return_value

    def test_thread_worker(self, tag):
        """
        Arguments:
            tag -- test tag (string)
        """
        #TODO if specific pipeline is specified for a test, acquire that specific pipeline
        pipeline = self.acquire_pipeline(tag) # find a free pipeline (blocking)
        rospy.loginfo("Acquired pipeline %s" % pipeline)
        # runSUT
        rospy.loginfo("[%s] Running SUT..." % pipeline)
        if self.execute_system(pipeline, 'SUT', 'run'):
            # runTestIt
            rospy.loginfo("[%s] Running TestIt..." % pipeline)
            if self.execute_system(pipeline, 'TestIt', 'run'):
                rospy.loginfo("[%s] Executing tests in TestIt container..." % pipeline)
                self.tests[tag]['result'] = self.execute_in_testit_container(pipeline, tag)
                self.tests[tag]['executor_pipeline'] = pipeline
                self.test_threads[tag]['result'] = self.tests[tag]['result']
                # stopTestIt
                rospy.loginfo("[%s] Stopping TestIt container..." % pipeline)
                self.execute_system(pipeline, 'TestIt', 'stop')
            else:
                # unable to run TestIt
                rospy.logerr("[%s] Unable to run TestIt!" % pipeline)
            # stopSUT
            rospy.loginfo("[%s] Stopping SUT..." % pipeline)
            self.execute_system(pipeline, 'SUT', 'stop')
        else:
            # Unable to run SUT
            rospy.logerr("[%s] Unable to run SUT!" % pipeline)
        if self.pipelines[pipeline]['state'] not in ["TEARDOWN", "OFFLINE", "FAILED"]:
            rospy.loginfo("Freeing pipeline \'%s\'" % pipeline)
            self.pipelines[pipeline]['state'] = "READY"

    def nonblocking_test_monitor(self):
        while True:
            sleep = True
            for thread in self.test_threads:
                if not self.test_threads[thread]['result'] is None:
                    del self.test_threads[thread]
                    sleep = False
                    break
            if len(self.test_threads) == 0:
                self.testing = False
                return # all threads are finished
            if sleep:
                time.sleep(0.5)

    def handle_test(self, req):
        rospy.logdebug("Test requested")
        result = True
        message = ""
        if not self.testing:
            self.testing = True
            for test in self.tests: # key
                self.tests[test]['result'] = None
                self.tests[test]['pipeline'] = None
                thread = threading.Thread(target=self.test_thread_worker, args=(test,))
                self.test_threads[test] = {'thread': thread, 'result': None}
                thread.start()
            thread = threading.Thread(target=self.nonblocking_test_monitor)
            thread.start()
        else:
            rospy.logerr("Unable to start tests! Tests are already executing!")
        return testit.srv.CommandResponse(result, message)

    def handle_results(self, req):
        rospy.logdebug("Results requested")
        message = ""
        result = True
        testsuite = testit.junit.testsuite(tests=len(self.tests))
        output = cStringIO.StringIO()
        for test in self.tests:
            testcase = testit.junit.testcase(classname=test)
            test_result = self.tests[test].get('result', None)
            if test_result is None:
                # skipped or not executed
                skipped = testit.junit.skipped(message="SKIPPED")
                skipped.set_valueOf_("This test has not been executed.")
                testcase.add_skipped(skipped)
                testcase.set_name("skipped")
            else:
                if not test_result:
                    # failed
                    failure = testit.junit.failure(message="FAILURE")
                    failure.set_valueOf_("Failure text")
                    testcase.add_failure(failure)
                    testcase.set_name("fail")
                else:
                    # success
                    testcase.set_name("success")
            testsuite.add_testcase(testcase)
        testsuite.export(output, 0, pretty_print=False)
        message = '<?xml version="1.0" encoding="UTF-8" ?>\n' + output.getvalue() + "\n"
        return testit.srv.CommandResponse(result, message)

    def handle_bag(self, req):
        """
        Use rosbag_merge to combine individual bag files and copy the file into working dir
        """
        result = False
        message = ""
        #bag_result = subprocess.call( "docker exec -d " + self.pipelines[pipeline]['testitHost'] + " /bin/bash -c \'source /opt/ros/$ROS_VERSION/setup.bash && cd /testit_tests/01/ && rosbag record -a --split --max-splits=" + str(self.tests[test]['bagMaxSplits']) + " --duration=" + str(self.tests[test]['bagDuration']) + " -O testit __name:=testit_rosbag_recorder\'", shell=True)
        return testit.srv.CommandResponse(result, message)

    def handle_coverage(self, req):
        rospy.logdebug("Coverage results requested")
        message = "coverage message"
        result = True
        return testit.srv.CommandResponse(result, message)

    def ground_path(self, command):
        """
        Process paths with bash commands.
        E.g., '$(rospack find testit)/data/' to '/home/user/catkin_ws/src/testit/testit/data/'
        """
        process = subprocess.Popen(['/bin/bash', '-c', 'echo ' + command + ''], stdout=subprocess.PIPE)
        out, err = process.communicate()
        out = out.replace("\n", "")
        return out

    def annotate_uppaal_transition(self, tree, entry):
        """
        Annotate a single uppaal transition.
        
        Only updates if entry lines

        Arguments:
        tee -- the xml.etree.ElementTree tree
        entry -- a single testit coverage entry

        Returns:
        Annotated xml.etree.ElementTree
        """
        assignments = tree.findall("./template/transition//*[@kind='assignment']")
        for assignment in assignments:
            failed = False
            for i, variable_dict in enumerate(entry['state']):
                for variable in variable_dict:
                    match = "i_" + entry['name'] + "_" + variable + "=" + str(entry['state'][i][variable])
                    if match not in assignment.text:
                        failed = True
                        break
                if failed:
                    break
            if not failed:
                if "V=" not in assignment.text:
                    assignment.text += ", V=" + str(entry['sum'])
        return tree

    def handle_uppaal_annotate_coverage(self, req):
        """
        Annotate Uppaal TA model (xml file) with coverage info.
        """
        message = "annotate message"
        result = True
        #TODO check req.args for specific test to process
        for test in self.tests:
            rospy.loginfo("Processing '%s'..." % test)
            model = self.tests[test].get('uppaalModel', None)
            if model is not None:

                # Read previous processed log entries
                data_directory = self.ground_path(self.configuration['dataDirectory'])
                rospy.loginfo("Data directory path is '%s'" % data_directory)
                coverage = []
                daemon_coverage_fullname = data_directory + "testit_coverage.log"
                try:
                    coverage = testit_common.parse_yaml(daemon_coverage_fullname)
                except:
                    rospy.logwarn("Could not open previous coverage file '%s'!" % daemon_coverage_fullname)

                rospy.loginfo("Uppaal model is %s" % model)
                pipeline = self.tests[test].get('executor_pipeline', None)
                if not pipeline:
                    rospy.logwarn("Test has not been executed during this runtime, unable to match data to pipeline!")
                else:
                    rospy.loginfo("Ran in %s " % pipeline)
                    # Get coverage log file from pipeline
                    #TODO support finding the log file in case it has been remapped in test adapter launch file
                    filename = "testit_tests/results/testit_coverage.log"
                    if self.pipelines[pipeline]['testItConnection'] != "-":
                        #TODO add support for remote testit pipeline (scp to temp file then read)
                        # command_prefix = "scp "
                        pass
                    # ground testItVolume path
                    path = self.ground_path(self.pipelines[pipeline]['testItVolume'])
                    fullname = path + filename
                    # Read the file
                    rospy.loginfo("Reading coverage log from file '%s'" % fullname)
                    data = None
                    try:
                        data = testit_common.parse_yaml(fullname)
                        rospy.loginfo("Read %s log entries!" % len(data))
                        # Add model info to daemon coverage log file (combined from all pipelines and over runs)
                        for entry in data:
                            entry['model'] = path + 'testit_tests/' + model
                        coverage += data
                    except:
                        rospy.logerr("Unable to open log file '%s'!" % fullname)
                    if data is not None:
                        # Remove the processed log file so we don't process it again
                        rospy.loginfo("Removing log file '%s'" % result)
                        result = subprocess.call("rm -f " + fullname, shell=True)
                        if result != 0:
                            rospy.logerr("Unable to remove log file '%s'!" % fullname)

                if len(coverage) > 0:
                    rospy.loginfo("Processing %s coverage entries..." % len(coverage))
                    # Create pruned list (keep only relevant model entries)
                    entries = []
                    rospy.loginfo("Filtering '%s' file entries..." % req.args)
                    for entry in coverage:
                        if model in entry['model']:
                            #TODO add PRE events, but only for advanced annotation algorithm
                            if req.args in entry['file'] and entry['event'] == "POST":
                                entries.append(entry)
                    rospy.loginfo("Pruned list is %s entries!" % len(entries))
                    if len(entries) > 0:
                        # Parse Uppaal model
                        rospy.loginfo("Parsing Uppaal model...")
                        root = None
                        try:
                            tree = xml.etree.ElementTree.parse(entries[0]['model'])
                            root = tree.getroot()
                        except Exception as e:
                            rospy.logerr("Unable to parse Uppaal model!")
                            import traceback
                            traceback.print_exc()

                        #TODO consider nondeterminism in traces
                        trace = []
                        for entry in entries:
                            if entry['traceStartTimestamp'] == entries[-1]['traceStartTimestamp']:
                                trace.append(entry)
                        rospy.loginfo("Annotating model with trace size %s..." % len(trace))
                        maxV = 0
                        for entry in trace:
                            if entry['sum'] > maxV:
                                maxV = entry['sum']
                            tree = self.annotate_uppaal_transition(tree, entry)
                        # Add variable V and add variable "maxV" as constant to model
                        declaration = tree.findall("./declaration")
                        if len(declaration) > 0:
                            declaration[0].text += " int V; const int maxV=" + str(maxV) + ";"
                        else:
                            rospy.logerr("Unable to find '<declaration>' tag in XML tree!")
                    
                        # Save annotated Uppaal model to file
                        annotated_file = data_directory + "annotated_models/" + model
                        annotated_directory = "/".join(annotated_file.split("/")[:-1])
                        result = subprocess.call("mkdir -p " + annotated_directory, shell=True)
                        if result != 0:
                            rospy.logerr("Unable to create directory '%s'!" % annotated_directory)
                        else:
                            rospy.loginfo("Writing annotated Uppaal model file to '%s'..." % annotated_file)
                            tree.write(annotated_file)

                        # Save coverage list to file
                        rospy.loginfo("Saving coverage list to file...")
                        testit_common.write_yaml_to_file(coverage, daemon_coverage_fullname)
                    else:
                        rospy.logerr("No entries found for file '%s'!" % req.args)
                
                
        
        return testit.srv.CommandResponse(result, message)

if __name__ == "__main__":
    rospy.init_node('testit_daemon')
    testit_daemon = TestItDaemon()
    rospy.loginfo("TestIt daemon started...")
    rospy.spin()
    rospy.loginfo("Shut down everything!")
