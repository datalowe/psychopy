#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""A PsychoPy drawing tool
Inspired by rockNroll87q - https://github.com/rockNroll87q/pyDrawing
"""

# Part of the PsychoPy library
# Copyright (C) 2018 Jonathan Peirce
# Distributed under the terms of the GNU General Public License (GPL).

from __future__ import absolute_import, print_function
from psychopy import event, logging
from .shape import ShapeStim
from .basevisual import MinimalStim

__author__ = 'David Bridges'

class Brush(MinimalStim):
    """A class for creating a freehand drawing tool.

    """
    def __init__(self,
                 win,
                 lineWidth=1.5,
                 lineColor=(1.0, 1.0, 1.0),
                 lineColorSpace='rgb',
                 opacity=1.0,
                 closeShape=False,
                 name=None,
                 depth=0,
                 autoLog=None,
                 autoDraw=False
                 ):

        super(Brush, self).__init__(name=name,
                                    autoLog=False)

        self.win = win
        self.name = name
        self.depth = depth
        self.lineColor = lineColor
        self.lineColorSpace = lineColorSpace
        self.lineWidth = lineWidth
        self.opacity = opacity
        self.closeShape = closeShape
        self.pointer = event.Mouse(win=self.win)
        self.shapes = []
        self.brushPos = []
        self.strokeIndex = -1
        self.atStartPoint = False

        self.autoLog = autoLog
        self.autoDraw = autoDraw

        if self.autoLog:
            # TODO: Set logging messages
            logging.exp("Creating {name}".format(name=self.name))

    def _resetVertices(self):
        """
        Resets list of vertices passed to ShapeStim
        """
        self.brushPos = []

    def _createStroke(self):
        """
        Creates ShapeStim for each stroke
        """
        self.shapes.append(ShapeStim(self.win,
                                     vertices=[[0, 0]],
                                     closeShape=self.closeShape,
                                     lineWidth=self.lineWidth,
                                     lineColor=self.lineColor,
                                     lineColorSpace=self.lineColorSpace,
                                     opacity=self.opacity,
                                     autoLog=True,
                                     autoDraw=True))

    @property
    def currentShape(self):
        return len(self.shapes) - 1

    @property
    def brushDown(self):
        """
        Checks whether the mouse button has been clicked in order to start drawing
        """
        return self.pointer.getPressed()[0] == 1

    def onBrushDown(self):
        """
        On first brush stroke, empty pointer position list, and create a new shapestim
        """
        if self.brushDown and not self.atStartPoint:
            self.atStartPoint = True
            self._resetVertices()
            self._createStroke()

    def onBrushDrag(self):
        """
        Check whether the brush is down. If brushDown is True, the brush path is drawn on screen
        """
        if self.brushDown:
            self.brushPos.append(self.pointer.getPos())
            self.shapes[self.currentShape].setVertices(self.brushPos)
        else:
            self.atStartPoint = False

    def draw(self):
        """
        Get starting stroke and begin painting on screen
        """
        self.onBrushDown()
        self.onBrushDrag()

    def reset(self):
        """
        Clear ShapeStim objects
        """
        if len(self.shapes):
            for shape in self.shapes:
                shape.setAutoDraw(False)
        self.atStartPoint = False
        self.shapes = []

    def setLineColor(self, value):
        """
        Sets the line color passed to ShapeStim

        Parameters
        ----------
        value
            Line color
        """
        self.lineColor = value

    def setLineWidth(self, value):
        """
        Sets the line width passed to ShapeStim

        Parameters
        ----------
        value
            Line width in pixels
        """
        self.lineWidth = value

    def setOpacity(self, value):
        """
        Sets the line opacity passed to ShapeStim

        Parameters
        ----------
        value
            Opacity range(0, 1)
        """
        self.opacity = value