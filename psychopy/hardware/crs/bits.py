#!/usr/bin/env python
#coding=utf-8

# Part of the PsychoPy library
# Copyright (C) 2014 Jonathan Peirce
# Distributed under the terms of the GNU General Public License (GPL).

#Acknowledgements:
#    This code was mostly written by Jon Peirce.
#    CRS Ltd provided support as needed.
#    Shader code for mono++ and color++ modes was based on code in Psythtoolbox
#    (Kleiner) but does not actually use that code directly

__docformat__ = "restructuredtext en"

DEBUG=True
import os, sys, time, glob, weakref
import numpy as np
import shaders
from copy import copy
from psychopy import logging, core, visual
from psychopy.hardware import serialdevice
import serial
import pyglet.gl as GL

plotResults = False
if plotResults:
    from matplotlib import pyplot

try:
    from psychopy.ext import _bits
    haveBitsDLL=True
except:
    haveBitsDLL=False

if DEBUG: #we don't want error skipping in debug mode!
    import shaders
    haveShaders=True
else:
    try:
        import shaders
        haveShaders=True
    except:
        haveShaders=False

try:
    import configparser
except:
    import ConfigParser as configparser

#bits++ modes
bits8BITPALETTEMODE=  0x00000001  #/* normal vsg mode */
NOGAMMACORRECT     =  0x00004000  #/* Gamma correction mode */
GAMMACORRECT       =  0x00008000  #/* Gamma correction mode */
VIDEOENCODEDCOMMS  =  0x00080000 # needs to be set so that LUT is read from screen

class BitsPlusPlus(object):
    """The main class to control a bits++ box.

    This is usually a class added within the window object and is typically accessed from there.
    e.g.::

        from psychopy import visual
        win = visual.Window([800,600], mode='bits++')
        win.bits.setContrast(0.5)#use bits++ to reduce the whole screen contrast by 50%

    :params:
        - rampType: 'cfgFile', None or an integer
            if 'cfgFile' then we'll look for a valid config in the userPrefs folder
            if an integer then this will be used during win.setGamma(rampType=rampType)
    """
    def __init__(self,
                    win,
                    contrast=1.0,
                    gamma=None,
                    nEntries=256,
                    mode='bits++',
                    rampType = 'configFile'):
        self.win = win
        self.contrast=contrast
        self.nEntries=nEntries
        self.mode = mode
        self.method = 'fast' #used to allow setting via USB which was 'slow'
        self.gammaCorrect = 'software' #Bits++ doesn't do its own correction so we need to

        if self.gammaCorrect=='software':
            if gamma is None:
                self.gamma = win.gamma #inherit from window
            elif len(gamma)>2: # [Lum,R,G,B] or [R,G,B]
                self.gamma=gamma[-3:]
            else:
                self.gamma = [gamma, gamma, gamma]

        if init():
            setVideoMode(NOGAMMACORRECT|VIDEOENCODEDCOMMS)
            self.initialised=True
            logging.debug('found and initialised bits++')
        else:
            self.initialised=False
            logging.warning("couldn't initialise bits++")

        #do the processing
        self._HEADandLUT = np.zeros((524,1,3),np.uint8)
        self._HEADandLUT[:12,:,0] = np.asarray([ 36, 63, 8, 211, 3, 112, 56, 34,0,0,0,0]).reshape([12,1])#R
        self._HEADandLUT[:12,:,1] = np.asarray([ 106, 136, 19, 25, 115, 68, 41, 159,0,0,0,0]).reshape([12,1])#G
        self._HEADandLUT[:12,:,2] = np.asarray([ 133, 163, 138, 46, 164, 9, 49, 208,0,0,0,0]).reshape([12,1])#B
        self.LUT=np.zeros((256,3),'d')        #just a place holder
        self.setLUT()#this will set self.LUT and update self._LUTandHEAD
        self._setupShaders()
        #replace window methods with our custom ones
        self.win._prepareFBOrender = self._prepareFBOrender
        self.win._finishFBOrender = self._finishFBOrender
        self.win._afterFBOrender = self._afterFBOrender
        #set gamma of the window to the identity LUT
        if rampType == 'configFile':
            #now check that we have a valid configuration of the box
            self.config = Config(self)
            #check that this matches the prev config for our graphics card etc
            ok=False #until we find otherwise
            ok = self.config.quickCheck()
            if ok:
                self.win.gammaRamp = self.config.identityLUT
            else:
                rampType = None
        if not rampType == 'configFile': #'this must NOT be an `else` from the above `if` because can be overidden
            #possibly we were given a numerical rampType (as in the :func:`psychopy.gamma.setGamma()`)
            self.win.winHandle.setGamma(self.win.winHandle, rampType=rampType)

    def setLUT(self,newLUT=None, gammaCorrect=True, LUTrange=1.0):
        """Sets the LUT to a specific range of values in Bits++ mode only

        Note that, if you leave gammaCorrect=True then any LUT values you supply
        will automatically be gamma corrected.

        The LUT will take effect on the next `Window.flip()`

        **Examples:**
            ``bitsBox.setLUT()``
                builds a LUT using bitsBox.contrast and bitsBox.gamma

            ``bitsBox.setLUT(newLUT=some256x1array)``
                (NB array should be float 0.0:1.0)
                Builds a luminance LUT using newLUT for each gun
                (actually array can be 256x1 or 1x256)

            ``bitsBox.setLUT(newLUT=some256x3array)``
               (NB array should be float 0.0:1.0)
               Allows you to use a different LUT on each gun

        (NB by using BitsBox.setContr() and BitsBox.setGamma() users may not
        need this function)
        """

        #choose endpoints
        LUTrange=np.asarray(LUTrange)
        if LUTrange.size==1:
            startII = int(round((0.5-LUTrange/2.0)*255.0))
            endII = int(round((0.5+LUTrange/2.0)*255.0))+1 #+1 because python ranges exclude last value
        elif LUTrange.size==2:
            multiplier=1.0
            if LUTrange[1]<=1: multiplier=255.0
            startII= int(round(LUTrange[0]*multiplier))
            endII = int(round(LUTrange[1]*multiplier))+1 #+1 because python ranges exclude last value
        stepLength = 2.0/(endII-startII-1)

        if newLUT is None:
            #create a LUT from scratch (based on contrast and gamma)
            #rampStep = 2.0/(self.nEntries-1)
            ramp = np.arange(-1.0,1.0+stepLength, stepLength)
            ramp = (ramp*self.contrast+1.0)/2.0
            #self.LUT will be stored as 0.0:1.0 (gamma-corrected)
            self.LUT[startII:endII,0] = copy(ramp)
            self.LUT[startII:endII,1] = copy(ramp)
            self.LUT[startII:endII,2] = copy(ramp)
        elif type(newLUT) in [float, int] or (newLUT.shape==()):
            self.LUT[startII:endII,0]= newLUT
            self.LUT[startII:endII,1]= newLUT
            self.LUT[startII:endII,2]= newLUT
        elif len(newLUT.shape) == 1: #one dimensional LUT
            #replicate LUT to other channels
            #check range is 0:1
            if newLUT>1.0:
                logging.warning('newLUT should be float in range 0.0:1.0')
            self.LUT[startII:endII,0]= copy(newLUT.flat)
            self.LUT[startII:endII,1]= copy(newLUT.flat)
            self.LUT[startII:endII,2]= copy(newLUT.flat)

        elif len(newLUT.shape) == 2: #one dimensional LUT
            #use LUT as is
            #check range is 0:1
            if max(max(newLUT))>1.0:
                raise AttributeError, 'newLUT should be float in range 0.0:1.0'
            self.LUT[startII:endII,:]= newLUT

        else:
            logging.warning('newLUT can be None, nx1 or nx3')

        #do gamma correction if necessary
        if gammaCorrect==True:
            gamma=self.gamma
            if hasattr(self.win.monitor, 'lineariseLums'):
                self.LUT[startII:endII, : ] = self.win.monitor.lineariseLums(self.LUT[startII:endII, : ], overrideGamma=gamma)

        #update the bits++ box with new LUT
        #get bits into correct order, shape and add to header
        ramp16 = (self.LUT*(2**16-1)).astype(np.uint16) #go from ubyte to uint16
        ramp16 = np.reshape(ramp16,(256,1,3))
        #set most significant bits
        self._HEADandLUT[12::2,:,:] = (ramp16[:,:,:]>>8).astype(np.uint8)
        #set least significant bits
        self._HEADandLUT[13::2,:,:] = (ramp16[:,:,:]&255).astype(np.uint8)
        self._HEADandLUTstr = self._HEADandLUT.tostring()

    def _drawLUTtoScreen(self):
        """(private) Used to set the LUT in Bits++ mode.

        Should not be needed by user if attached to a ``psychopy.visual.Window()``
        since this will automatically draw the LUT as part of the screen refresh.
        """
        #push the projection matrix and set to orthorgaphic
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glOrtho( 0, self.win.size[0],self.win.size[1], 0, 0, 1 )    #this also sets the 0,0 to be top-left
        #but return to modelview for rendering
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()

        #draw the pixels
        GL.glActiveTextureARB(GL.GL_TEXTURE0_ARB)
        GL.glEnable(GL.GL_TEXTURE_2D)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glActiveTextureARB(GL.GL_TEXTURE1_ARB)
        GL.glEnable(GL.GL_TEXTURE_2D)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glRasterPos2i(0,1)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glDrawPixels(len(self._HEADandLUT),1,
            GL.GL_RGB,GL.GL_UNSIGNED_BYTE,
            self._HEADandLUTstr)
        #GL.glDrawPixels(524,1, GL.GL_RGB,GL.GL_UNSIGNED_BYTE, self._HEADandLUTstr)
        #return to 3D mode (go and pop the projection matrix)
        GL.glMatrixMode( GL.GL_PROJECTION )
        GL.glPopMatrix()
        GL.glMatrixMode( GL.GL_MODELVIEW )

    def setContrast(self,contrast,LUTrange=1.0, gammaCorrect=None):
        """Set the contrast of the LUT for bits++ mode only

        :Parameters:
            contrast : float in the range 0:1
                The contrast for the range being set
            LUTrange : float or array
                If a float is given then this is the fraction of the LUT to be used.
                If an array of floats is given, these will specify the start/stop points
                as fractions of the LUT. If an array of ints (0-255) is given these
                determine the start stop *indices* of the LUT

        Examples:
            ``setContrast(1.0,0.5)``
                will set the central 50% of the LUT so that a stimulus with
                contr=0.5 will actually be drawn with contrast 1.0

            ``setContrast(1.0,[0.25,0.5])``

            ``setContrast(1.0,[63,127])``
                will set the lower-middle quarter of the LUT
                (which might be useful in LUT animation paradigms)

        """
        self.contrast = contrast
        if gammaCorrect is None:
            if gammaCorrect not in [False, "hardware"]:
                gammaCorrect = False
            else:
                gammaCorrect = True
        #setLUT uses contrast automatically
        self.setLUT(newLUT=None, gammaCorrect=gammaCorrect, LUTrange=LUTrange)

    def setGamma(self, newGamma):
        """Set the LUT to have the requested gamma value
        Currently also resets the LUT to be a linear contrast
        ramp spanning its full range. May change this to read
        the current LUT, undo previous gamm and then apply
        new one?"""
        self.gamma=newGamma
        self.setLUT() #easiest way to update
    def reset(self):
        """Deprecated: This was used on the old Bits++ to power-cycle the box
        It required the compiled dll, which only worked on windows and doesn't
        work with Bits#
        """
        reset()
    def _setupShaders(self):
        """creates and stores the shader programs needed for mono++ and color++ modes
        """
        if not haveShaders:
            return
        self._shaders={}
        self._shaders['mono++'] = shaders.compileProgram(shaders.vertSimple,
            shaders.bitsMonoModeFrag)
        self._shaders['color++'] = shaders.compileProgram(shaders.vertSimple,
            shaders.bitsColorModeFrag)
    def _prepareFBOrender(self):
        if self.mode=='mono++':
            GL.glUseProgram(self._shaders['mono++'])
        elif self.mode=='color++':
            GL.glUseProgram(self._shaders['color++'])
        else:
            GL.glUseProgram(self.win._progFBOtoFrame)
    def _finishFBOrender(self):
        GL.glUseProgram(0)
    def _afterFBOrender(self):
        if self.mode.startswith('bits'):
            self._drawLUTtoScreen()

class BitsSharp(BitsPlusPlus, serialdevice.SerialDevice):
    """A class to support functions of the Bits#

    This device uses the CDC (serial port) connection to the bits box. To use it
    you must have followed the instructions from CRS Ltd to get your box into
    the CDC communication mode.

    On windows you must specify the COM port name.
    On OSX, if you don't specify a port then the first match of /dev/tty.usbmodemfa* will be used
    ON linux, if you don't specify a port then /dev/ttyS0 will be used

    :params:

    win: a PsychoPy :ref:`~psychopy.visual.Window` object (required)

    portName: the (virtual) serial port to which the device is connected. If None
        then PsychoPy will search available serial ports and test communication

    mode: 'bits++', 'color++', 'mono++', 'status'

    checkConfigLevel: integer
        Allows you to specify how much checking of the device is done to ensure
        a valid identity look-up table. If you specify one level and it fails
        then the check will be escalated to the next level (e.g. if we check
        level 1 and find that it fails we try to find a new LUT):
            0 don't check at all
            1 check that the graphics driver and OS version haven't changed since last LUT calibration
            2 check that the current LUT calibration still provides identity (requires switch to status mode)
            3 search for a new identity look-up table (requires switch to status mode)
    """
    name='CRS Bits#'
    def __init__(self, win=None, portName=None, mode='', checkConfigLevel=1):

        serialdevice.SerialDevice.__init__(self, port=portName, baudrate=19200,
                 byteSize=8, stopBits=1,
                 parity="N", #'N'one, 'E'ven, 'O'dd, 'M'ask,
                 eol='\n',
                 maxAttempts=1, pauseDuration=0.1,
                 checkAwake=True)
        if not self.OK:
            return

        #the following are used by bits++ mode
        self._HEADandLUT = np.zeros((524,1,3),np.uint8)
        self._HEADandLUT[:12,:,0] = np.asarray([ 36, 63, 8, 211, 3, 112, 56, 34,0,0,0,0]).reshape([12,1])#R
        self._HEADandLUT[:12,:,1] = np.asarray([ 106, 136, 19, 25, 115, 68, 41, 159,0,0,0,0]).reshape([12,1])#G
        self._HEADandLUT[:12,:,2] = np.asarray([ 133, 163, 138, 46, 164, 9, 49, 208,0,0,0,0]).reshape([12,1])#B
        self.LUT=np.zeros((256,3),'d')        #just a place holder

        #replace window methods with our custom ones
        self.win = win
        self.win._prepareFBOrender = self._prepareFBOrender
        self.win._finishFBOrender = self._finishFBOrender
        self.win._afterFBOrender = self._afterFBOrender

        #we have a confirmed connection. Now check details about device and system
        if not hasattr(self, 'info'):
            self.info = self.getInfo()
        self.config = None
        self.mode = mode
        if self.win is not None:
            if not hasattr(self.win, '_prepareFBOrender'):
                logging.error("BitsSharp was given an object as win argument but this is not a visual.Window")
            self.win._prepareFBOrender = self._prepareFBOrender
            self.win._finishFBOrender = self._finishFBOrender
            self._setupShaders()
            #now check that we have a valid configuration of the box
            if checkConfigLevel:
                ok = self.checkConfig(level=checkConfigLevel)
            else:
                self.win.gammaRamp = self.config.identityLUT
        else:
            self.config = None # makes no sense if we have a window?
            logging.warning("%s was not given any PsychoPy win" %(self))

    def __del__(self):
        """If the user discards this object then close the serial port so it is released"""
        if hasattr(self, 'com'):
            self.com.close()

    def isAwake(self):
        """Test whether we have an active connection on the virtual serial port
        """
        self.info = self.getInfo()
        return len(self.info['ProductType'])>0 #if we got a productType then this is a bits device

    def getInfo(self):
        """Returns a python dictionary of info about the Bits Sharp box
        """
        self.read(timeout=0.5) #clear input buffer
        info={}
        #get product ('Bits_Sharp'?)
        self.sendMessage('$ProductType\r')
        time.sleep(0.1)
        info['ProductType'] = self.read().replace('#ProductType;','').replace(';\n\r','')
        #get serial number
        self.sendMessage('$SerialNumber\r')
        time.sleep(0.1)
        info['SerialNumber'] = self.read().replace('#SerialNumber;','').replace('\x00\n\r','')
        #get firmware date
        self.sendMessage('$FirmwareDate\r')
        time.sleep(0.1)
        info['FirmwareDate'] = self.read().replace('#FirmwareDate;','').replace(';\n\r','')
        return info

    @property
    def mode(self):
        """Get/set the mode of the BitsSharp to one of:
            "bits++"
            "mono++" (not currently working?)
            "color++" (not currently working?)
            "status"
            "storage"
            "auto"
        """
        return self.__dict__['mode']

    @mode.setter
    def mode(self, value):
        if value in [None, '']:
            self.__dict__['mode'] = ''
        elif ('mode' in self.__dict__) and value==self.mode:
            return #nothing to do here. Move along please
        elif value=='status':
            self.sendMessage('$statusScreen\r')
            self.__dict__['mode'] = 'status'
        elif 'storage' in value.lower():
            self.sendMessage('$USB_massStorage\r')
            self.__dict__['mode'] = 'massStorage'
            logging.info('Switched %s to %s mode' %(self.info['ProductType'], self.__dict__['mode']))
        elif value.startswith('bits'):
            self.sendMessage('$BitsPlusPlus\r')
            self.__dict__['mode'] = 'bits++'
            self.setLUT()
            logging.info('Switched %s to %s mode' %(self.info['ProductType'], self.__dict__['mode']))
        elif value.startswith('mono'):
            self.sendMessage('$monoPlusPlus\r')
            self.__dict__['mode'] = 'mono++'
            logging.info('Switched %s to %s mode' %(self.info['ProductType'], self.__dict__['mode']))
        elif value.startswith('colo'):
            self.sendMessage('$colorPlusPlus\r')
            self.__dict__['mode'] = 'color++'
            logging.info('Switched %s to %s mode' %(self.info['ProductType'], self.__dict__['mode']))
        elif value.startswith('auto'):
            self.sendMessage('$autoPlusPlus\r')
            self.__dict__['mode'] = 'auto++'
            logging.info('Switched %s to %s mode' %(self.info['ProductType'], self.__dict__['mode']))

    def setLUT(self,newLUT=None, gammaCorrect=False, LUTrange=1.0, contrast=None):
        """SetLUT is only really needed for bits++ mode of bits# to set the
        look-up table (256 values with 14bits each).

        For the BitsPlusPlus device the deafult is to perform gamma correction
        here but on the BitsSharp it seems better to have the device perform
        that itself as the last step so gamma correction is off here by default.

        If no contrast has yet been set (it isn't needed for other modes) then
        it will be set to 1 here.

        """
        if contrast is not None: # we were given a new contrast value so use it
            self.contrast = contrast
        elif not hasattr(self, 'contrast'): #we don't have one yet so create a default
            self.contrast = 1.0
        BitsPlusPlus.setLUT(self, newLUT, gammaCorrect, LUTrange)

    @property
    def temporalDithering(self):
        """Temporal dithering can be set to True or False
        """
        return self.__dict__['temporalDithering']
    @temporalDithering.setter
    def temporalDithering(self, value):
        if value:
            self.sendMessage('$TemporalDithering=[ON]\r')
        else:
            self.sendMessage('$TemporalDithering=[OFF]\r')
        self.__dict__['temporalDithering'] = value

    @property
    def gammaCorrectFile(self):
        """The gamma correction file to be used
        """
        return self.__dict__['gammaCorrectFile']
    @gammaCorrectFile.setter
    def gammaCorrectFile(self, value):
        self.sendMessage('$enableGammaCorrection=[%s]\r' %(value))
        self.__dict__['gammaCorrectFile'] = value

    @property
    def monitorEDID(self):
        """Stores/sets the EDID file for the monitor.
        The edid files will be located in the EDID subdirectory of the flash disk.
        The file “automatic.edid” will be the file read from the connected monitor
        """
        return self.__dict__['monitorEDID']
    @monitorEDID.setter
    def monitorEDID(self, value):
        self.sendMessage('$setMonitorType=[%s]\r' %(value))
        self.__dict__['monitorEDID'] = value

    #functions
    def beep(self, freq=800, dur=1):
        """Make a beep with the internal
        """
        self.sendMessage('$Beep=[%i, %.4f]\r' %(freq, dur))

    def getVideoLine(self, lineN, nPixels, timeout=1.0, nAttempts=10):
        """Return the r,g,b values for a number of pixels on a particular video line

        :param lineN: the line number you want to read

        :param nPixels: the number of pixels you want to read

        :param nAttempts: the first time you call this function it has to get to status mode.
            In this case it sometimes takes a few attempts to make the call work

        :return: an Nx3 numpy array of uint8 values
        """
        #define sub-function oneAttempt
        def oneAttempt():
            self.com.flushInput()
            self.sendMessage('$GetVideoLine=[%i, %i]\r' %(lineN, nPixels))
            self.__dict__['mode'] = 'status' #the box implicitly ends up in status mode
            #prepare to read
            t0 = time.time()
            raw=""
            vals=[]
            while len(vals)<(nPixels*3):
                raw += self.read(timeout=0.001)
                vals = raw.split(';')[1:-1]
                if  (time.time()-t0)>timeout:
                    logging.warn("getVideoLine() timed out: only found %i pixels in %.2f s" %(len(vals), timeout))
                    return []
            return np.array(vals, dtype=int).reshape([-1,3])
        #call oneAttempt a few times
        for attempt in range(nAttempts):
            vals = oneAttempt()
            if len(vals):
                return vals
        return None

    def read(self, timeout=0.1):
        """Get the current waiting characters from the serial port if there are any
        """
        self.com.setTimeout(timeout)
        nChars = self.com.inWaiting()
        raw = self.com.read(nChars)
        if raw: #don't bother if we found nothing on input
            logging.debug("Got BitsSharp reply: %s" %(repr(raw)))
        return raw

    #TO DO: The following are either not yet implemented (or not tested)
    def start(self):
        raise NotImplemented
    def stop(self):
        raise NotImplemented

    def checkConfig(self, level=1, demoMode=False, logFile=''):
        """Checks whether there is a configuration for this device and whether it's correct

        :params:

                level: integer

                    0: do nothing

                    1: check that we have a config file and that the graphics
                    card and operating system match that specified in the file. Then assume
                    identity LUT is correct

                    2: switch the box to status mode and check that the
                    identity LUT is currently working

                    3: force a fresh search for the identity LUT
        """
        prevMode = self.mode
        #if we haven't fetched a config yet then do so
        if not self.config:
            self.config = Config(self)
        #check that this matches the prev config for our graphics card etc
        ok=False #until we find otherwise
        if level==1:
            ok = self.config.quickCheck()
            if not ok:
                #didn't match our graphics card or OS
                level=2
                self._warnTesting()
            else:
                self.mode = prevMode
                logging.info("Bits# config matches current system: %s on %s" %(self.config.gfxCard, self.config.os))
                return 1
        #it didn't so switch to doing the test
        if level==2:
            errs = self.config.testLUT(demoMode=demoMode)
            if (errs**2).sum() != 0:
                level=3
                logging.info("Bits# found a config file but the LUT didn't work as identity. We'll try to find a working one.")
            else:
                self.config.identityLUT = self.win.winHandle.getGammaRamp(self.win.winHandle).transpose()
                self.config.save()
                self.mode = prevMode
                logging.info("We found a LUT in the config file and it worked as identity")
                return 1
        if level==3:
            ok = self.config.findIdentityLUT(demoMode=demoMode, logFile=logFile)
        self.mode = prevMode
        return ok

    def _warnTesting(self):
        msg = "We need to run some tests on your graphics card (hopefully just once).\n" +\
            "The BitsSharp will go into status mode while this is done.\n" +\
            "It can take a minute or two..."
        print(msg)
        sys.stdout.flush()
        msgOnScreen = visual.TextStim(self.win, msg)
        msgOnScreen.draw()
        self.win.flip()
        core.wait(1.0)
        self.win.flip()

    #properties that need a weak ref to avoid circular references
    @property
    def win(self):
        """The window that this box is attached to
        """
        if self.__dict__.get('win') is None:
            return None
        else:
            return self.__dict__.get('win')()
    @win.setter
    def win(self, win):
        self.__dict__['win'] = weakref.ref(win)

class Config(object):
    def __init__(self, bits):
        #we need to set bits reference using weakref to avoid circular refs
        self.bits=bits
        self.load() #try to fetch previous config file
        self.logFile = 0 #replace with a file handle if opened

    def load(self, filename=None):
        """If name is None then we'll try to save to
        """
        def parseLUTLine(line):
            return line.replace('[','').replace(']','').split(',')

        if filename is None:
            from psychopy import prefs
            filename = os.path.join(prefs.paths['userPrefsDir'], 'crs_bits.cfg')
        if os.path.exists(filename):
            config = configparser.RawConfigParser()
            with open(filename) as f:
                config.readfp(f)
            self.os = config.get('system','os')
            self.gfxCard = config.get('system','gfxCard')
            self.identityLUT = np.ones([256,3])
            self.identityLUT[:,0] = parseLUTLine(config.get('identityLUT','r'))
            self.identityLUT[:,1] = parseLUTLine(config.get('identityLUT','g'))
            self.identityLUT[:,2] = parseLUTLine(config.get('identityLUT','b'))
            return True
        else:
            logging.warn('no config file yet for %s' %self.bits)
            self.identityLUT = None
            self.gfxCard = None
            self.os = None
            return False

    def _getGfxCardString(self):
        from pyglet.gl import gl_info
        thisStr = "%s: %s" %(gl_info.get_renderer(), gl_info.get_version())
        return thisStr

    def _getOSstring(self):
        import platform
        return platform.platform()

    def save(self, filename=None):
        if filename is None:
            from psychopy import prefs
            filename = os.path.join(prefs.paths['userPrefsDir'], 'crs_bits.cfg')
            logging.info('saved Bits# config file to %r' %filename)
        #create the config object
        config = configparser.RawConfigParser()
        config.add_section('system')
        self.os = config.set('system','os', self._getOSstring())
        self.gfxCard = config.set('system','gfxCard', self._getGfxCardString())

        #save the current LUT
        config.add_section('identityLUT')
        config.set('identityLUT','r',list(self.identityLUT[:,0]))
        config.set('identityLUT','g',list(self.identityLUT[:,1]))
        config.set('identityLUT','b',list(self.identityLUT[:,2]))

        #save it to disk
        with open(filename, 'w') as fileObj:
            config.write(fileObj)
        logging.info("Saved %s configuration to %s" %(self.bits, filename))

    def quickCheck(self):
        """Check whether the current graphics card and OS match those of the last saved LUT
        """
        if self._getGfxCardString() != self.gfxCard:
            logging.warn("The graphics card or its driver has changed. We'll re-check the identity LUT for the card")
            return 0
        if self._getOSstring() != self.os:
            logging.warn("The OS has been changed/updated. We'll re-check the identity LUT for the card")
            return 0
        return 1 #all seems the same as before

    def testLUT(self,LUT=None, demoMode=False):
        """Apply a LUT to the graphics card gamma table and test whether we get back 0:255
        in all channels.

        :params:

            LUT: The lookup table to be tested (256x3). If None then the LUT will not be altered

        :returns:

            a 256x3 array of error values (integers in range 0:255)
        """
        bits = self.bits #if you aren't yet in
        win = self.bits.win
        if LUT is not None:
            win.gammaRamp = LUT
        #create the patch of stimulus to test
        expectedVals = range(256)
        w,h = win.size
        testArrLums = np.resize(np.linspace(-1,1,256),[256,256]) #NB psychopy uses -1:1
        stim = visual.ImageStim(win, image=testArrLums,
            size=[256,h], pos=[128-w/2,0], units='pix',
            )
        expected = np.repeat(expectedVals,3).reshape([-1,3])
        stim.draw()
        #make sure the frame buffer was correct (before gamma was applied)
        frm = np.array(win.getMovieFrame(buffer='back'))
        assert np.alltrue(frm[0,0:256,0]==range(256))
        win.flip()
        #use bits sharp to test
        if demoMode:
            return [0]*256
        pixels = bits.getVideoLine(lineN=5, nPixels=256)
        errs = pixels-expected
        if self.logFile:
            for ii, channel in enumerate('RGB'):
                self.logFile.write(channel)
                for pixVal in pixels[:,ii]:
                    self.logFile.write(', %i' %pixVal)
                self.logFile.write('\n')
        return errs

    def findIdentityLUT(self, maxIterations = 1000, errCorrFactor = 1.0/2048, # amount of correction done for each iteration
        nVerifications = 50, #number of repeats (successful) to check dithering has been eradicated
        demoMode = True, #generate the screen but don't go into status mode
        logFile = '',
        ):
        """Search for the identity LUT for this card/operating system.
        This requires that the window being tested is fullscreen on the Bits#
        monitor (or at least occupys the first 256 pixels in the top left corner!)

        :params:

            LUT: The lookup table to be tested (256x3). If None then the LUT will not be altered

        :returns:

            a 256x3 array of error values (integers in range 0:255)
        """
        t0 = time.time()
        #create standard options
        LUTs = {}
        LUTs['intel'] = np.repeat(np.linspace(.05,.95,256),3).reshape([-1,3])
        LUTs['0-255'] = np.repeat(np.linspace(0,1.0,256),3).reshape([-1,3])
        LUTs['0-65535'] = np.repeat(np.linspace(0.0, 65535.0/65536.0, num=256),3).reshape([-1,3])
        LUTs['1-65536'] = np.repeat(np.linspace(0.0, 65535.0/65536.0, num=256),3).reshape([-1,3])

        if logFile:
            self.logFile = open(logFile,'w')

        if plotResults:
            pyplot.Figure()
            pyplot.plot([0,255],[0,255], '-k')
            errPlot = pyplot.plot(range(256), range(256), '.r')[0]
            pyplot.show(block=False)

        lowestErr = 1000000000
        bestLUTname = None
        logging.flush()
        for LUTname, currentLUT in LUTs.items():
            print 'Checking %r LUT:' %(LUTname),
            errs = self.testLUT(currentLUT, demoMode)
            if plotResults:
                errPlot.set_ydata(range(256)+errs[:,0])
                pyplot.draw()
            print 'mean err = %.3f per LUT entry' %(abs(errs).mean())
            if abs(errs).mean()< abs(lowestErr):
                lowestErr = abs(errs).mean()
                bestLUTname = LUTname
        if lowestErr==0:
            print "The %r identity LUT produced zero error. We'll use that!" %(LUTname)
            return

        print "Best was %r LUT (mean err = %.3f). Optimising that..." %(bestLUTname, lowestErr)
        currentLUT = LUTs[bestLUTname]
        errProgression=[]
        corrInARow=0
        for n in range(maxIterations):
            errs = self.testLUT(currentLUT)
            tweaks = errs*errCorrFactor
            currentLUT -= tweaks
            currentLUT[currentLUT>1] = 1.0
            currentLUT[currentLUT<0] = 0.0
            meanErr = abs(errs).mean()
            errProgression.append(meanErr)
            if plotResults:
                errPlot.set_ydata(range(256)+errs[:,0])
                pyplot.draw()
            if meanErr>0:
                print "%.3f" %meanErr,
                corrInARow=0
            else:
                print ".",
                corrInARow+=1
            if corrInARow>=nVerifications:
                print 'success in a total of %.1fs' %(time.time()-t0)
                self.identityLUT = currentLUT
                self.save() #it worked so save this configuration for future
                break
            elif len(errProgression)>10 and max(errProgression)-min(errProgression)<0.001:
                print "Trying to correct the gamma table was having no effect. Make sure the window was fullscreen and on the Bits# screen"
                break

        #did we get here by failure?!
        if n==(maxIterations-1):
            print "failed to converge on a successful identity LUT. This is BAD!"

        if plotResults:
            pyplot.figure(figsize=[18,12])
            pyplot.subplot(1,3,1)
            pyplot.plot(errProgression)
            pyplot.title('Progression of errors')
            pyplot.ylabel("Mean error per LUT entry (0-1)")
            pyplot.xlabel("Test iteration")
            r256 = np.reshape(range(256),[256,1])
            pyplot.subplot(1,3,2)
            pyplot.plot(r256, r256, 'k-')
            pyplot.plot(r256, currentLUT[:,0]*255, 'r.', markersize=2.0)
            pyplot.plot(r256, currentLUT[:,1]*255, 'g.', markersize=2.0)
            pyplot.plot(r256, currentLUT[:,2]*255, 'b.', markersize=2.0)
            pyplot.title('Final identity LUT')
            pyplot.ylabel("LUT value")
            pyplot.xlabel("LUT entry")

            pyplot.subplot(1,3,3)
            deviations = currentLUT-r256/255.0
            pyplot.plot(r256, deviations[:,0], 'r.')
            pyplot.plot(r256, deviations[:,1], 'g.')
            pyplot.plot(r256, deviations[:,2], 'b.')
            pyplot.title('LUT deviations from sensible')
            pyplot.ylabel("LUT value")
            pyplot.xlabel("LUT deviation (multiples of 1024)")
            pyplot.savefig("bitsSharpIdentityLUT.pdf")
            pyplot.show()

    #Some properties for which we need weakref pointers, not std properties
    @property
    def bits(self):
        """The bits box to which this config object refers
        """
        if self.__dict__.get('bits') is None:
            return None
        else:
            return self.__dict__.get('bits')()
    @bits.setter
    def bits(self, bits):
        self.__dict__['bits'] = weakref.ref(bits)

def init():
    """DEPRECATED: we used to initialise Bits++ via the compiled dll

    This only ever worked on windows and BitsSharp doesn't need it at all

    Note that, by default, bits++ will perform gamma correction
    that you don't want (unless you have the CRS calibration device)
    (Recommended that you use the BitsPlusPlus class rather than
    calling this directly)
    """
    retVal = False
    if haveBitsDLL:
        try:
            retVal = _bits.bitsInit() #returns null if fails?
        except:
            logging.error('bits.init() barfed!')
    return retVal

def setVideoMode(videoMode):
    """set the video mode of the bits++ (win32 only)

    bits8BITPALETTEMODE         =  0x00000001  #normal vsg mode

    NOGAMMACORRECT      =  0x00004000  #No gamma correction mode

    GAMMACORRECT            =  0x00008000  #Gamma correction mode

    VIDEOENCODEDCOMMS =  0x00080000

    (Recommended that you use the BitsLUT class rather than
    calling this directly)
    """
    if haveBitsDLL:
        return _bits.bitsSetVideoMode(videoMode)
    else:
        return 1

def reset(noGamma=True):
    """reset the bits++ box via the USB cable by initialising again
    Allows the option to turn off gamma correction
    """
    OK = init()
    if noGamma and OK:
        setVideoMode(NOGAMMACORRECT)
