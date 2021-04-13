# -*- coding: utf-8 -*-
# Part of the psychopy.iohub library.
# Copyright (C) 2012-2016 iSolver Software Solutions
# Distributed under the terms of the GNU General Public License (GPL).
from psychopy.iohub.errors import print2err, printExceptionDetailsToStdErr
from psychopy.iohub.constants import EyeTrackerConstants

from .mock_tobiiwrapper import MockTobiiTracker
from ..mouse import EyeTracker as BaseMouseTracker


class EyeTracker(BaseMouseTracker):
    """
    To start iohub with a Mouse Simulated eye tracker, add the full iohub device name
    as a kwarg passed to launchHubServer::

        eyetracker.hw.mouse.EyeTracker

    Examples:
        A. Start ioHub with the Mouse Simulated eye tracker::

            from psychopy.iohub import launchHubServer
            from psychopy.core import getTime, wait

            iohub_config = {'eyetracker.hw.mouse.EyeTracker': {}}

            io = launchHubServer(**iohub_config)

            # Get the eye tracker device.
            tracker = io.devices.tracker

        B. Print all eye tracker events received for 2 seconds::

            # Check for and print any eye tracker events received...
            tracker.setRecordingState(True)

            stime = getTime()
            while getTime()-stime < 2.0:
                for e in tracker.getEvents():
                    print(e)

        C. Print current eye position for 5 seconds::

            # Check for and print current eye position every 100 msec.
            stime = getTime()
            while getTime()-stime < 5.0:
                print(tracker.getPosition())
                wait(0.1)

            tracker.setRecordingState(False)

            # Stop the ioHub Server
            io.quit()

    In addition to using mouse simulation, this class specifically mocks
    part of ioHub's tobii eyetracker interface (runSetupProcedure),
    to enable the use and development of custom calibration procedures.
    """

    def __init__(self, *args, **kwargs):
        BaseMouseTracker.__init__(self, *args, **kwargs)

        # Attach mocked tobii wrapper
        EyeTracker._tobii = MockTobiiTracker()

    def runSetupProcedure(self):
        """
        This holds the same code as ioHub's tobii eyetracker
        runSetupProcedure method, save for the import statement, which has
        been changed to avoid having to create a duplicate
        file for TobiiPsychopyCalibrationGraphics. The difference between
        this class' and the 'real' class' runSetupProcedure lies in
        the fact that this class uses a mocked version
        of the tobii_research wrapper, in
        `self._tobii` (which is accessed by TobiiPsychopyCalibrationGraphics).
        """
        try:
            from ..tobii.tobiiCalibrationGraphics import TobiiPsychopyCalibrationGraphics

            calibration_properties = self.getConfiguration().get('calibration')
            screenColor = calibration_properties.get(
                'screen_background_color')                     # [r,g,b] of screen

            genv = TobiiPsychopyCalibrationGraphics(
                self, screenColor=screenColor)

            calibrationOK = genv.runCalibration()

            # On some graphics cards, we have to minimize before
            # closing or the calibration window will stay visible
            # after close is called.
            genv.window.winHandle.set_visible(False)
            genv.window.winHandle.minimize()

            genv.window.close()

            genv._unregisterEventMonitors()
            genv.clearAllEventBuffers()

            return calibrationOK

        except Exception:
            print2err('Error during runSetupProcedure')
            printExceptionDetailsToStdErr()
        return EyeTrackerConstants.EYETRACKER_ERROR
