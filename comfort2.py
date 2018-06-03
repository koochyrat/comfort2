# Copyright(c) 2018 Khor Chin Heong (koochyrat@gmail.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import select
import socket
import time
import datetime
import threading
from datetime import timedelta
import homeassistant
import paho.mqtt.client as mqtt

DOMAIN = "comfort2"
ALARMSTATETOPIC = DOMAIN+"/alarm"
ALARMCOMMANDTOPIC = DOMAIN+"/alarm/set"
ALARMAVAILABLETOPIC = DOMAIN+"/alarm/online"
ALARMMESSAGETOPIC = DOMAIN+"/alarm/message"
ALARMTIMERTOPIC = DOMAIN+"/alarm/timer"

ALARMINPUTTOPIC = DOMAIN+"/input%d"   #input1,input2,... input96 for every input
ALARMVIRTUALINPUTRANGE = range(17,65)   #set this according to your system
ALARMINPUTCOMMANDTOPIC = DOMAIN+"/input%d/set"   #input17,input18,... input64 for virtual inputs

ALARMNUMBEROFOUTPUTS = 16    #set this according to your system
ALARMOUTPUTTOPIC = DOMAIN+"/output%d" #output1,output2,... for every output
ALARMOUTPUTCOMMANDTOPIC = DOMAIN+"/output%d/set" #output1/set,output2/set,... for every output

ALARMNUMBEROFRESPONSES = 255    #set this according to your system
ALARMRESPONSECOMMANDTOPIC = DOMAIN+"/response%d/set" #response1,response2,... for every response

ALARMNUMBEROFFLAGS = 255    #set this according to your system
ALARMFLAGTOPIC = DOMAIN+"/flag%d"   #flag1,flag2,...flag255
ALARMFLAGCOMMANDTOPIC = DOMAIN+"/flag%d/set" #flag1/set,flag2/set,... flag255/set

MQTTBROKERIP = "localhost"
MQTTBROKERPORT = 1883
MQTTUSERNAME = ""
MQTTPASSWORD = ""
COMFORTIP = "192.168.123.88"
COMFORTPORT = 8008
PINCODE = "1234"
BUFFER_SIZE = 4096
TIMEOUT = timedelta(seconds=30) #Comfort will disconnect if idle for 120 secs, so make sure this is less than that
RETRY = timedelta(seconds=5)

class ComfortLUUserLoggedIn(object):
    def __init__(self, datastr="", user=0):
        if datastr:
            self.user = int(datastr[2:4], 16)
        else:
            self.user = int(user)

class ComfortIPInputActivationReport(object):
    def __init__(self, datastr="", input=0, state=0):
        if datastr:
            self.input = int(datastr[2:4], 16)
            self.state = int(datastr[4:6], 16)
        else:
            self.input = int(input)
            self.state = int(state)

class ComfortOPOutputActivationReport(object):
    def __init__(self, datastr="", output=0, state=0):
        if datastr:
            self.output = int(datastr[2:4], 16)
            self.state = int(datastr[4:6], 16)
        else:
            self.output = int(output)
            self.state = int(state)

class ComfortFLFlagActivationReport(object):
    def __init__(self, datastr="", flag=0, state=0):
        if datastr:
            self.flag = int(datastr[2:4], 16)
            self.state = int(datastr[4:6], 16)
        else:
            self.flag = int(flag)
            self.state = int(state)

class ComfortZ_ReportAllZones(object):
    def __init__(self, data={}):
        self.inputs = []
        b = (len(data) - 2) // 2   #variable number of zones reported
        for i in range(1,b+1):
            inputbits = int(data[2*i:2*i+2],16)
            for j in range(0,8):
                self.inputs.append(ComfortIPInputActivationReport("", 8*(i-1)+1+j,(inputbits>>j) & 1))

class ComfortY_ReportAllOutputs(object):
    def __init__(self, data={}):
        self.outputs = []
        b = (len(data) - 2) // 2   #variable number of outputs reported
        for i in range(1,b+1):
            outputbits = int(data[2*i:2*i+2],16)
            for j in range(0,8):
                self.outputs.append(ComfortOPOutputActivationReport("", 8*(i-1)+1+j,(outputbits>>j) & 1))

class Comfortf_ReportAllFlags(object):
    def __init__(self, data={}):
        self.flags = []
        b = (len(data) - 4) // 2   #b = 32
        for i in range(2,b+2):
            flagbits = int(data[2*i:2*i+2],16)
            for j in range(0,8):
                self.flags.append(ComfortFLFlagActivationReport("", 8*(i-2)+1+j,(flagbits>>j) & 1))

#mode = { 00=Off, 01=Away, 02=Night, 03=Day, 04=Vacation }
class ComfortM_SecurityModeReport(object):
    def __init__(self, data={}):
        self.mode = int(data[2:4],16)
        if self.mode == 0: self.modename = "disarmed"
        elif self.mode == 1: self.modename = "armed_away"
        elif self.mode == 2: self.modename = "armed_night"
        elif self.mode == 3: self.modename = "armed_home"
        elif self.mode == 4: self.modename = "armed_vacation"

#zone = 00 means system can be armed, no open zones
class ComfortERArmReadyNotReady(object):
    def __init__(self, data={}):
        self.zone = int(data[2:4],16)

class ComfortAMSystemAlarmReport(object):
    def __init__(self, data={}):
        self.alarm = int(data[2:4],16)
        self.triggered = True   #for comfort alarm state Alert, Trouble, Alarm
        self.parameter = int(data[4:6],16)
        if self.alarm == 0: self.message = "Intruder, Zone "+str(self.parameter)
        elif self.alarm == 1: self.message = "Zone "+str(self.parameter)+" Trouble"
        elif self.alarm == 2: self.message = "Low Battery"
        elif self.alarm == 3: self.message = "Power Failure"
        elif self.alarm == 4: self.message = "Phone Trouble"
        elif self.alarm == 5: self.message = "Duress"
        elif self.alarm == 6: self.message = "Arm Failure"
        elif self.alarm == 8: self.message = "Disarm"; self.triggered = False
        elif self.alarm == 9: self.message = "Arm"; self.triggered = False
        elif self.alarm == 10: self.message = "Tamper"
        elif self.alarm == 12: self.message = "Entry Warning, Zone "+str(self.parameter); self.triggered = False
        elif self.alarm == 13: self.message = "Alarm Abort"; self.triggered = False
        elif self.alarm == 14: self.message = "Siren Tamper"
        elif self.alarm == 15: self.message = "Bypass, Zone "+str(self.parameter); self.triggered = False
        elif self.alarm == 17: self.message = "Dial Test"; self.triggered = False
        elif self.alarm == 19: self.message = "Entry Alert, Zone "+str(self.parameter); self.triggered = False
        elif self.alarm == 20: self.message = "Fire"
        elif self.alarm == 21: self.message = "Panic"
        elif self.alarm == 22: self.message = "GSM Trouble"
        elif self.alarm == 23: self.message = "New Message"; self.triggered = False
        elif self.alarm == 24: self.message = "Doorbell"; self.triggered = False
        elif self.alarm == 25: self.message = "Comms Failure RS485"
        elif self.alarm == 26: self.message = "Signin Tamper"

class ComfortEXEntryExitDelayStarted(object):
    def __init__(self, data={}):
        self.type = int(data[2:4],16)
        self.delay = int(data[4:6],16)

class Comfort2(mqtt.Client):
    def init(self, mqtt_ip, mqtt_port, mqtt_username, mqtt_password, comfort_ip, comfort_port, comfort_pincode):
        self.mqtt_ip = mqtt_ip
        self.mqtt_port = mqtt_port
        self.comfort_ip = comfort_ip
        self.comfort_port = comfort_port
        self.comfort_pincode = comfort_pincode
        self.connected = False
        self.username_pw_set(mqtt_username, mqtt_password)

    # The callback for when the client receives a CONNACK response from the server.
    def on_connect(self, client, userdata, flags, rc):
        print("Connected with result code "+str(rc))
        self.subscribe(ALARMCOMMANDTOPIC)
        for i in range(1, ALARMNUMBEROFOUTPUTS + 1):
            self.subscribe(ALARMOUTPUTCOMMANDTOPIC % i)
        for i in range(1, ALARMNUMBEROFRESPONSES + 1):
            self.subscribe(ALARMRESPONSECOMMANDTOPIC % i)
        for i in ALARMVIRTUALINPUTRANGE: #for virtual inputs 17-64
            self.subscribe(ALARMINPUTCOMMANDTOPIC % i)
        for i in range(1, ALARMNUMBEROFFLAGS + 1):
            self.subscribe(ALARMFLAGCOMMANDTOPIC % i)
        self.readcurrentstate()

    def on_disconnect(self, client, userdata, rc):
        print("Disconnected with result code "+str(rc))

    # The callback for when a PUBLISH message is received from the server.
    def on_message(self, client, userdata, msg):
        #print("on message")
        msgstr = msg.payload.decode()
        print(msg.topic+" "+msgstr)
        if msg.topic == ALARMCOMMANDTOPIC:
            if self.connected:
                if msgstr == "ARM_HOME":
                    self.comfortsock.sendall(("\x03m!03"+self.comfort_pincode+"\r").encode()) #arm to 03 day mode
                elif msgstr == "ARM_AWAY":
                    self.comfortsock.sendall(("\x03m!01"+self.comfort_pincode+"\r").encode()) #arm to 01 away mode
                elif msgstr == "DISARM":
                    self.comfortsock.sendall(("\x03m!00"+self.comfort_pincode+"\r").encode()) #arm to 00 disarm mode
        elif msg.topic.startswith(DOMAIN+"/output") and msg.topic.endswith("/set"):
            output = int(msg.topic.split("/")[1][6:])
            state = int(msgstr)
            if self.connected:
                self.comfortsock.sendall(("\x03O!%02X%02X\r" % (output, state)).encode())
        elif msg.topic.startswith(DOMAIN+"/response") and msg.topic.endswith("/set"):
            response = int(msg.topic.split("/")[1][8:])
            if self.connected:
                self.comfortsock.sendall(("\x03R!%02X\r" % response).encode())
        elif msg.topic.startswith(DOMAIN+"/input") and msg.topic.endswith("/set"):
            virtualinput = int(msg.topic.split("/")[1][5:])
            state = int(msgstr)
            if self.connected:
                self.comfortsock.sendall(("\x03I!%02X%02X\r" % (virtualinput, state)).encode())
        elif msg.topic.startswith(DOMAIN+"/flag") and msg.topic.endswith("/set"):
            flag = int(msg.topic.split("/")[1][4:])
            state = int(msgstr)
            if self.connected:
                self.comfortsock.sendall(("\x03F!%02X%02X\r" % (flag, state)).encode())

    def on_publish(self, client, obj, mid):
        #print("mid: " + str(mid))
        pass
    def on_subscribe(self, client, userdata, mid, granted_qos):
        #print("subscribed "+str(userdata))
        pass

    def on_log(self, client, userdata, level, buf):
        #print("log: ",buf)
        pass

    def entryexit_timer(self):
        #print("timer: "+str(self.entryexitdelay))
        self.publish(ALARMTIMERTOPIC, self.entryexitdelay)
        self.entryexitdelay -= 1
        if self.entryexitdelay >= 0:
            threading.Timer(1, self.entryexit_timer).start()
    
    def readlines(self, recv_buffer=BUFFER_SIZE, delim='\r'):
        buffer = ''
        data = True
        while data:
            try:
                data = self.comfortsock.recv(recv_buffer).decode()
            except socket.timeout as e:
                err = e.args[0]
                # this next if/else is a bit redundant, but illustrates how the
                # timeout exception is setup
                if err == 'timed out':
                    #sleep(1)
                    #print ('recv timed out, retry later')
                    self.comfortsock.sendall("\x03cc00\r".encode()) #echo command for keepalive
                    continue
                else:
                    print (e)
    #sys.exit(1)
            except socket.error as e:
                # Something else happened, handle error, exit, etc.
                print (e)
                raise
                #sys.exit(1)
            else:
                if len(data) == 0:
                    print ('orderly shutdown on server end')
                #sys.exit(0)
                else:
                    # got a message do something :)
                    buffer += data
                    
                    while buffer.find(delim) != -1:
                        line, buffer = buffer.split('\r', 1)
                        yield line
        return
    
    def login(self):
        self.comfortsock.sendall(("\x03LI"+self.comfort_pincode+"\r").encode())

    def readcurrentstate(self):
        if self.connected == True:
            #get security mode
            self.comfortsock.sendall("\x03M?\r".encode())
            #get all zone input states
            self.comfortsock.sendall("\x03Z?\r".encode())
            #get all output states
            self.comfortsock.sendall("\x03Y?\r".encode())
            #get all flag states
            self.comfortsock.sendall("\x03f?00\r".encode())
            self.publish(ALARMAVAILABLETOPIC, 1)
            self.publish(ALARMMESSAGETOPIC, "")

    def setdatetime(self):
        if self.connected == True:  #set current date and time
            now = datetime.datetime.now()
            self.comfortsock.sendall(("\x03DT%02d%02d%02d%02d%02d%02d\r" % (now.year, now.month, now.day, now.hour, now.minute, now.second)).encode())

    def run(self):
        self.connect_async(self.mqtt_ip, self.mqtt_port, 60)
        self.loop_start()
        self.publish(ALARMAVAILABLETOPIC, 0)
        try:
            while True:
                try:
                    self.comfortsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    print("connecting to "+self.comfort_ip+" "+str(self.comfort_port))
                    self.comfortsock.connect((self.comfort_ip, self.comfort_port))
                    self.comfortsock.settimeout(TIMEOUT.seconds)
                    self.login()

                    for line in self.readlines():
                        if line[1:] != "cc00":
                            print(line[1:])
                        if line[0] == "\x03":   #check for valid prefix
                            if line[1:3] == "LU":
                                luMsg = ComfortLUUserLoggedIn(line[1:])
                                if luMsg.user != 0:
                                    print("login ok")
                                    self.connected = True
                                    #client.publish(ALARMSTATETOPIC, "disarmed")
                                    self.publish(ALARMCOMMANDTOPIC, "comm test")
                                    self.setdatetime()
                                    self.readcurrentstate()
                            elif line[1:3] == "IP":
                                ipMsg = ComfortIPInputActivationReport(line[1:])
                                print("input %d state %d" % (ipMsg.input, ipMsg.state))
                                self.publish(ALARMINPUTTOPIC % ipMsg.input, ipMsg.state)
                            elif line[1:3] == "Z?":
                                zMsg = ComfortZ_ReportAllZones(line[1:])
                                for ipMsgZ in zMsg.inputs:
                                    #print("input %d state %d" % (ipMsgZ.input, ipMsgZ.state))
                                    self.publish(ALARMINPUTTOPIC % ipMsgZ.input, ipMsgZ.state)
                            elif line[1:3] == "M?" or line[1:3] == "MD":
                                mMsg = ComfortM_SecurityModeReport(line[1:])
                                print("alarm mode "+mMsg.modename)
                                self.publish(ALARMSTATETOPIC, mMsg.modename)
                                self.entryexitdelay = 0    #zero out the countdown timer
                            elif line[1:3] == "ER":
                                erMsg = ComfortERArmReadyNotReady(line[1:])
                                if not erMsg.zone == 0:
                                    print("zone not ready: "+str(erMsg.zone))
                                    self.comfortsock.sendall("\x03KD1A\r".encode()) #force arm
                            elif line[1:3] == "AM":
                                amMsg = ComfortAMSystemAlarmReport(line[1:])
                                self.publish(ALARMMESSAGETOPIC, amMsg.message)
                                if amMsg.triggered:
                                    self.publish(ALARMSTATETOPIC, "triggered")
                            elif line[1:3] == "EX":
                                exMsg = ComfortEXEntryExitDelayStarted(line[1:])
                                self.entryexitdelay = exMsg.delay
                                self.entryexit_timer()
                                self.publish(ALARMSTATETOPIC, "pending")
                            elif line[1:3] == "RP":
                                self.publish(ALARMMESSAGETOPIC, "Phone Ring")
                            elif line[1:3] == "DB":
                                self.publish(ALARMMESSAGETOPIC, "Door Bell")
                            elif line[1:3] == "OP":
                                ipMsg = ComfortOPOutputActivationReport(line[1:])
                                print("output %d state %d" % (ipMsg.output, ipMsg.state))
                                self.publish(ALARMOUTPUTTOPIC % ipMsg.output, ipMsg.state)
                            elif line[1:3] == "Y?":
                                yMsg = ComfortY_ReportAllOutputs(line[1:])
                                for opMsgY in yMsg.outputs:
                                    #print("output %d state %d" % (opMsgY.output, opMsgY.state))
                                    self.publish(ALARMOUTPUTTOPIC % opMsgY.output, opMsgY.state)
                            elif line[1:3] == "f?":
                                fMsg = Comfortf_ReportAllFlags(line[1:])
                                for fMsgf in fMsg.flags:
                                    #print("flag %d state %d" % (fMsgf.flag, fMsgf.state))
                                    self.publish(ALARMFLAGTOPIC % fMsgf.flag, fMsgf.state)
                            elif line[1:3] == "FL":
                                flMsg = ComfortFLFlagActivationReport(line[1:])
                                print("flag %d state %d" % (flMsg.flag, flMsg.state))
                                self.publish(ALARMFLAGTOPIC % flMsg.flag, flMsg.state)
                            elif line[1:3] == "RS":
                                #on rare occassions comfort ucm might get reset (RS11), our session is no longer valid, need to relogin
                                print("reset detected")
                                self.login()
                except socket.error as v:
                    #errorcode = v[0]
                    print("socket error "+str(v))
                    #raise
                print("lost connection to comfort, reconnecting...")
                self.publish(ALARMAVAILABLETOPIC, 0)
                time.sleep(RETRY.seconds)
        finally:
            infot = self.publish(ALARMAVAILABLETOPIC, 0)
            infot.wait_for_publish()

mqttc = Comfort2(DOMAIN)
mqttc.init(MQTTBROKERIP, MQTTBROKERPORT, MQTTUSERNAME, MQTTPASSWORD, COMFORTIP, COMFORTPORT, PINCODE)
mqttc.run()
