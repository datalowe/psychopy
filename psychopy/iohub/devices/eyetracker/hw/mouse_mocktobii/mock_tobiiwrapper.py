"""ioHub Common Eye Tracker Interface for Tobii (C) Eye Tracking System."""
from __future__ import print_function
# -*- coding: utf-8 -*-
# Part of the psychopy.iohub library.
# Copyright (C) 2012-2016 iSolver Software Solutions
# Distributed under the terms of the GNU General Public License (GPL).

import numpy as np
from .....devices import Computer
from .....errors import print2err, printExceptionDetailsToStdErr

getTime = Computer.getTime

# MOCKED Tobii Eye Tracker / tobii_research package functionality wrapper
class MockTobiiTracker(object):
    try:
        CALIBRATION_STATUS_SUCCESS = tobii_research.CALIBRATION_STATUS_SUCCESS
    except:
        CALIBRATION_STATUS_SUCCESS = 1

    def __init__(self, serial_number=None,  model=None):
        """
        """
        if serial_number and model:
            print(
                f'Mocking tobii tracker with serial number {serial_number}'
                ', model {model}...'
            )
        else:
            print('Mocking tobii tracker...')
        self._eyetracker = 1
        self._last_eye_data = None
        self._isRecording = False

    def newScreenCalibration(self):
        if self._eyetracker:
            return MockScreenBasedCalibration(self._eyetracker)

# MOCKED version of tobii_research's ScreenBasedCalibration class
class MockScreenBasedCalibration:
    """
    This is a MOCKED version of the tobii_research package's
    ScreenBasedCalibration class. It only 'implements'/mocks methods
    that are used by the `TobiiPsychopyCalibrationGraphics` class.
    """
    class MockCalibrationStatus:
        """
        Represents mocked calibration status.
        """
        def __init__(self, status = None):
            self.status = status

    def __init__(self, eyetracker):
        self._eyetracker = eyetracker
        self._target_x_coords = []
        self._target_y_coords = []

    def enter_calibration_mode(self):
        """
        Always returns True.
        :return: bool
        """
        return True

    def collect_data(self, x_coord, y_coord):
        """
        Takes in calibration target screen coordinates and adds them to this
        mocked calibration's stored coordinates.
        :param x_coord: float
        :param y_coord: float
        """
        self._target_x_coords.append(x_coord)
        self._target_y_coords.append(y_coord)

    def leave_calibration_mode(self):
        """
        Always returns True.
        :return: bool
        """
        return True

    def compute_and_apply(self):
        """
        Always returns an object with a status
        attribute saying the calibration was a success.
        :return: object
        """
        mock_calibration_result = self.MockCalibrationStatus()
        mock_calibration_result.status = 'calibration_status_success'
        return mock_calibration_result
