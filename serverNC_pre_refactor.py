import os
import logging
import requests
from socket import *
from threading import Thread
import thread
import pytz
import time
import sys
#import Adafruit_DHT
import glob
import datetime
import RPi.GPIO as GPIO
from astral import Astral
from pololu_drv8835_rpi import motors, MAX_SPEED

# Hold either button for 2 seconds to switch modes
# In auto buttons Stop for 60 seconds. Again, continues
# In manual, left goes up assuming it's not up. right goes down assuming
#  any button while moving stops it
# Todo:
# Record how long it takes to open the door, close
# ERror states
#Setup Dynamic DNS


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

fh = logging.FileHandler('/tmp/log.log')
fh.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

fh.setFormatter(formatter)
ch.setFormatter(formatter)
logger.addHandler(fh)
logger.addHandler(ch)


class Coop(object):
    
    MAX_MOTOR_ON = 10 #longest time motor can be on
    
    TIMEZONE_CITY = 'Seattle'
    AFTER_SUNSET_DELAY = 30 #used below as minutes
    AFTER_SUNRISE_DELAY = 0 #original code had 3 hrs... presumably for egg production
    
    #MOTOR DIRECTIONS
    IDLE = 0
    UP = 1
    DOWN = 2
    
    #DOOR STATUS
    UNKNOWN = 0
    OPEN = 1
    CLOSED = 2
    EMERGENCY = 3
    
    #Triggering
    NOT_TRIGGERED = 1
    TRIGGERED = 0
    
    DOOR_OPEN = 20
    DOOR_CLOSED = 21



    def __init__(self):
        self.door_status = Coop.UNKNOWN
        self.started_motor = None 
        self.direction = Coop.IDLE

        self.mail_key = os.environ.get('MAILGUN_KEY') or exit('You need a key set')
        self.mail_url = os.environ.get('MAILGUN_URL') or exit('You need a key set')
        self.mail_recipient = os.environ.get('MAILGUN_RECIPIENT') or exit('You need a key set')


        a = Astral()
        self.city = a[Coop.TIMEZONE_CITY]
        self.setupPins()

        t1 = Thread(target = self.checkTriggers)
        t2 = Thread(target = self.checkTime)
        
        t1.setDaemon(True)
        t2.setDaemon(True)
        
        t1.start()
        t2.start()
        

        host = '192.168.1.199'
        port = 55567
        addr = (host, port)

        serversocket = socket(AF_INET, SOCK_STREAM)
        serversocket.bind(addr)
        serversocket.listen(2)

        
        self.stopDoor(0)

        while True:
            try:
                logger.info("Server is listening for connections\n")
                clientsocket, clientaddr = serversocket.accept() #this line is keeping everything running by blocking the thread from closing.
                thread.start_new_thread(self.handler, (clientsocket, clientaddr))
            except KeyboardInterrupt:
                break
            time.sleep(0.01)

        logger.info("Close connection")
        serversocket.close()
        self.stopDoor(0)

    def setupPins(self):
        GPIO.setmode(GPIO.BCM)

        GPIO.setup(Coop.DOOR_OPEN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(Coop.DOOR_CLOSED, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    def closeDoor(self):
        if (self.door_status == Coop.EMERGENCY):
            return
        (top, bottom) = self.currentTriggerStatus()
        if (bottom == Coop.TRIGGERED):
            logger.info("Door is already closed")
            self.door_status = Coop.CLOSED
            return
        logger.info("Closing door")
        self.started_motor = datetime.datetime.now()
        motors.setSpeeds(-MAX_SPEED,-MAX_SPEED)
        self.direction = Coop.DOWN

    def openDoor(self):
        if (self.door_status == Coop.EMERGENCY):
            return
        (top, bottom) = self.currentTriggerStatus()
        if (top == Coop.TRIGGERED):
            logger.info("Door is already open")
            self.door_status = Coop.OPEN
            return
        logger.info("Opening door")
        self.started_motor = datetime.datetime.now()
        motors.setSpeeds(MAX_SPEED,MAX_SPEED)
        self.direction= Coop.UP

    def stopDoor(self, delay):
        if self.direction != Coop.IDLE:
            logger.info("Stop door")
            time.sleep(delay)
            motors.setSpeeds(0,0)
            self.direction = Coop.IDLE
            self.started_motor = None

        (top, bottom) = self.currentTriggerStatus()
        if (top == Coop.TRIGGERED):
            logger.info("Door is open")
            self.door_status = Coop.OPEN
            self.sendEmail('Coop door is OPEN', 'Yay!')
        elif (bottom == Coop.TRIGGERED):
            logger.info("Door is closed")
            self.door_status = Coop.CLOSED
            self.sendEmail('Coop door is CLOSED', 'Yay!')
        else:
            logger.info("Door is in an unknown state")
            self.door_status = Coop.UNKNOWN




    def sendEmail(self, subject, content):
        logger.info("Sending email: %s" % subject)
#        try:
#            request = requests.post(
#                self.mail_url,
#                auth=("api", self.mail_key),
#                data={"from": "Chickens <mailgun@mailgun.dxxd.net>",
#                      "to": [self.mail_recipient],
#                      "subject": subject,
#                      "text": content}) 
#            #logger.info('Status: {0}'.format(request.status_code))
#        except Exception as e:
#            logger.error("Error: " + e)


    def checkTime(self):
        while True:
            if (self.door_status == Coop.EMERGENCY):
                return
            current = datetime.datetime.now(pytz.timezone(self.city.timezone))
            sun = self.city.sun(date=datetime.datetime.now(), local=True)

            after_sunset = sun["sunset"] + datetime.timedelta(minutes = Coop.AFTER_SUNSET_DELAY)
            after_sunrise = sun["sunrise"] + datetime.timedelta(minutes = Coop.AFTER_SUNRISE_DELAY) 

            if (current < after_sunrise or current > after_sunset) and self.door_status != Coop.CLOSED and self.direction != Coop.DOWN:
                logger.info("Door should be closed based on time of day")
                self.closeDoor()

            elif current > after_sunrise and current < after_sunset and self.door_status != Coop.OPEN and self.direction != Coop.UP:
                logger.info("Door should be open based on time of day")
                self.openDoor()
            time.sleep(1)

  

    def currentTriggerStatus(self):
        bottom = GPIO.input(Coop.DOOR_CLOSED)
        top = GPIO.input(Coop.DOOR_OPEN)
        return (top, bottom)

    def checkTriggers(self):
        while True:
            if (self.door_status == Coop.EMERGENCY):
                return
        
        
            (top, bottom) = self.currentTriggerStatus()
            if (self.direction == Coop.UP and top == Coop.TRIGGERED):
                logger.info("Top sensor triggered")
                self.stopDoor(0)
            if (self.direction == Coop.DOWN and bottom == Coop.TRIGGERED):
                logger.info("Bottom sensor triggered")
                self.stopDoor(2)#Reed switch trips before they make contact. Add a little time for the door to really close. no downside to being "too closed"

            # Check for issues
            if self.started_motor is not None:
                if (datetime.datetime.now() - self.started_motor).seconds > Coop.MAX_MOTOR_ON:
                    self.emergencyStopDoor("Ran too long, gonna cook them eggs")

            time.sleep(0.1)

    def emergencyStopDoor(self, reason):
                    ## Just shut it off no matter what
        self.door_status = Coop.EMERGENCY
        logger.info("Emergency Stop door: " + reason)
        motors.setSpeeds(0,0)
        self.direction = Coop.IDLE
        self.started_motor = None
        self.stopDoor(0)
        self.sendEmail('Coop Emergency STOP', reason)
        sys.exit(0)


    def handler(self, clientsocket, clientaddr):
        #logger.info("Accepted connection from: %s " % clientaddr)

        while True:
            data = clientsocket.recv(1024)
            if not data:
                break
            else:
                data = data.strip()
                if (data == 'stop'):
                    self.stopDoor(0)
                elif (data == 'open'):
                    self.openDoor()
                elif (data == 'close'):
                    self.closeDoor()
                elif (data == 'quit'):
                    break
                msg = "You sent me: %s \n" % data
                clientsocket.send(msg)
            time.sleep(0.01)
        clientsocket.close()

if __name__ == "__main__":
    coop = Coop()
