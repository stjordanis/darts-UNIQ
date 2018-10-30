from scipy.stats import entropy
from os import makedirs, path
from math import ceil
from io import BytesIO
from base64 import b64encode
from urllib.parse import quote
from numpy import linspace
from abc import abstractmethod

import torch.nn.functional as F
from torch import save as saveFile

import cnn.utils

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.backends.backend_pdf import PdfPages


class Statistics:
    entropyKey = 'alphas_entropy'
    alphaDistributionKey = 'alphas_distribution'
    lossVarianceKey = 'loss_variance'
    lossAvgKey = 'loss_avg'
    crossEntropyLossAvgKey = 'cross_entropy_loss_avg'
    bopsLossAvgKey = 'bops_loss_avg'
    bopsKey = 'bops'

    # set plot points style
    ptsStyle = '-'

    # set maxCols & minRows for multiplot
    nColsMax = 7
    nRowsDefault = 3

    def __init__(self, layersList, saveFolder):
        nLayers = len(layersList)
        # create plot folder
        plotFolderPath = '{}/plots'.format(saveFolder)
        if not path.exists(plotFolderPath):
            makedirs(plotFolderPath)

        self.saveFolder = plotFolderPath
        # init list of batch labels for y axis
        self.batchLabels = []
        # collect ops bitwidth per layer in model
        self.layersBitwidths = [layer.getAllBitwidths() for layer in layersList]
        # init containers
        self.containers = {
            self.entropyKey: [[] for _ in range(nLayers)],
            self.lossVarianceKey: [[]], self.alphaDistributionKey: [[[] for _ in range(layer.numOfOps())] for layer in layersList],
            self.lossAvgKey: [[]], self.crossEntropyLossAvgKey: [[]], self.bopsLossAvgKey: [[]]
        }
        # map each list we plot for all layers on single plot to filename
        self.plotAllLayersKeys = [self.entropyKey, self.lossAvgKey, self.crossEntropyLossAvgKey, self.bopsLossAvgKey, self.lossVarianceKey]
        self.plotLayersSeparateKeys = [self.alphaDistributionKey]
        # init colors map
        self.colormap = plt.cm.hot
        # init plots data dictionary
        self.plotsDataFilePath = '{}/plots.data'.format(saveFolder)
        self.plotsData = {}
        # init bopsData, which is a map where keys are labels (pts type) and values are list of tuples (bitwidth, bops, accuracy)
        self.bopsData = {}
        self.baselineLabel = 'Baseline'

    def addBatchData(self, model, nEpoch, nBatch):
        # add batch label
        self.batchLabels.append('[{}]_[{}]'.format(nEpoch, nBatch))
        # add data per layer
        for i, layer in enumerate(model.layersList):
            # calc layer alphas distribution
            probs = F.softmax(layer.alphas, dim=-1).detach()
            # save distribution
            for j, p in enumerate(probs):
                self.containers[self.alphaDistributionKey][i][j].append(p.item())
            # calc entropy
            self.containers[self.entropyKey][i].append(entropy(probs))

        # plot data
        self.plotData()

    def __saveAndPlotBops(self):
        # save data to plotData
        self.plotsData[self.bopsKey] = self.bopsData
        # save plots data
        saveFile(self.plotsData, self.plotsDataFilePath)
        # update plot
        self.plotBops(self.plotsData, self.bopsKey, self.baselineLabel, self.saveFolder)

    # bopsData_ is a map where keys are bitwidth and values are bops.
    # we need to find the appropriate checkpoint for accuracy values.
    def addBaselineBopsData(self, args, bopsData_):
        label = self.baselineLabel
        # init label list if label doesn't exist
        if label not in self.bopsData.keys():
            self.bopsData[label] = []

        # add data to list
        for bitwidth, bops in bopsData_.items():
            # load checkpoint
            checkpoint, _ = cnn.utils.loadCheckpoint(args.dataset, args.model, bitwidth)
            if checkpoint is not None:
                accuracy = checkpoint.get('best_prec1')
                if accuracy is not None:
                    self.bopsData[label].append((bitwidth, bops, accuracy))

        # save & plot bops
        self.__saveAndPlotBops()

    # bopsData_ is a dictionary where keys are labels and values are list of tuples of (bitwidth, bops, accuracy)
    def addBopsData(self, bopsData_):
        for label in bopsData_.keys():
            # init label list if label doesn't exist
            if label not in self.bopsData.keys():
                self.bopsData[label] = []

            # append values to self.bopsData
            self.bopsData[label].extend(bopsData_[label])

        # save & plot bops
        self.__saveAndPlotBops()

    @staticmethod
    def saveFigPDF(figs, fileName, saveFolder):
        pdf = PdfPages('{}/{}.pdf'.format(saveFolder, fileName))
        for fig in figs:
            pdf.savefig(fig)

        pdf.close()

    def saveFigHTML(self, figs, fileName):
        # create html page
        htmlCode = '<!DOCTYPE html><html><head><style>' \
                   'table { font-family: gisha; border-collapse: collapse;}' \
                   'td, th { border: 1px solid #dddddd; text-align: center; padding: 8px; white-space:pre;}' \
                   '.collapsible { background-color: #777; color: white; cursor: pointer; padding: 18px; border: none; text-align: left; outline: none; font-size: 15px; }' \
                   '.active, .collapsible:hover { background-color: #555; }' \
                   '.content { max-height: 0; overflow: hidden; transition: max-height 0.2s ease-out;}' \
                   '</style></head>' \
                   '<body>'
        for fig in figs:
            # convert fig to base64
            canvas = FigureCanvas(fig)
            png_output = BytesIO()
            canvas.print_png(png_output)
            img = b64encode(png_output.getvalue())
            img = '<img src="data:image/png;base64,{}">'.format(quote(img))
            # add image to html code
            htmlCode += img
        # close html tags
        htmlCode += '</body></html>'
        # write html code to file
        with open('{}/{}.html'.format(self.saveFolder, fileName), 'w') as f:
            f.write(htmlCode)

    @staticmethod
    def __setAxesProperties(ax, xLabel, yLabel, yMax, title, yMin=0.0):
        # ax.set_xticks(xValues)
        # ax.set_xticklabels(self.batchLabels)
        ax.set_xlabel(xLabel)
        ax.set_ylabel(yLabel)
        ax.set_ylim(top=yMax, bottom=yMin)
        ax.set_title(title)
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.005), ncol=5, fancybox=True, shadow=True)

    @staticmethod
    def __setFigProperties(fig, figSize=(15, 10)):
        fig.set_size_inches(figSize)
        fig.tight_layout()
        # close plot
        plt.close(fig)

    @staticmethod
    def setPlotProperties(fig, ax, xLabel, yLabel, yMax, title, yMin=0.0):
        Statistics.__setAxesProperties(ax, xLabel, yLabel, yMax, title, yMin)
        Statistics.__setFigProperties(fig)

    def __plotContainer(self, data, xValues, xLabel, yLabel, title, labelFunc, axOther=None, scale=True, annotate=None):
        # create plot
        fig, ax = plt.subplots(nrows=1, ncols=1)
        # init ylim values
        dataMax = 0
        dataSum = []
        # init flag to check whether we have plotted something or not
        isPlotEmpty = True
        # init colors
        colors = [self.colormap(i) for i in linspace(0.6, 0.0, len(data))]
        # reset plot data in plotsData dictionary
        self.plotsData[title] = dict(x=xValues, data=[])

        for i, layerData in enumerate(data):
            # add data to plotsData
            self.plotsData[title]['data'].append(dict(y=layerData, style=self.ptsStyle, label=labelFunc(i), color=colors[i]))
            # plot by shortest length between xValues, layerData
            plotLength = min(len(xValues), len(layerData))
            xValues = xValues[:plotLength]
            layerData = layerData[:plotLength]

            ax.plot(xValues, layerData, self.ptsStyle, label=labelFunc(i), c=colors[i])
            if axOther:
                axOther.plot(xValues, layerData, self.ptsStyle, label=labelFunc(i), c=colors[i])

            isPlotEmpty = False
            dataMax = max(dataMax, max(layerData))
            dataSum.append(sum(layerData) / len(layerData))

        if not isPlotEmpty:
            # add annotations if exists
            if annotate:
                for txt, pt in annotate:
                    ax.annotate(txt, pt)

            # set yMax
            yMax = dataMax * 1.1

            # don't scale axOther
            if axOther:
                if yLabel == self.alphaDistributionKey:
                    yMax = 1.1

                axOther.grid()
                self.__setAxesProperties(axOther, xLabel, yLabel, yMax, title)

            if scale:
                yMax = min(yMax, (sum(dataSum) / len(dataSum)) * 1.5)

            self.setPlotProperties(fig, ax, xLabel, yLabel, yMax, title)

        return fig

    # find optimal grid
    def __findGrid(self, nPlots):
        nRowsOpt, nColsOpt = 0, 0
        optDiff = None

        # iterate over options
        for nRows in range(1, self.nRowsDefault + 1):
            nCols = ceil(nPlots / nRows)
            # calc how many empty plots will be in grid
            diff = nRows * nCols - nPlots
            # update if it is a better grid
            if (nCols <= self.nColsMax) and ((optDiff is None) or (diff < optDiff)):
                nRowsOpt = nRows
                nColsOpt = nCols
                optDiff = diff

        # if we haven't found a valid grid, use nColsMax as number of cols and adjust number of rows accordingly
        if optDiff is None:
            nRowsOpt = ceil(nPlots, self.nColsMax)
            nColsOpt = self.nColsMax

        return nRowsOpt, nColsOpt

    def plotData(self):
        # set x axis values
        xValues = list(range(len(self.batchLabels)))
        # generate different plots
        for fileName in self.plotAllLayersKeys:
            data = self.containers[fileName]
            fig = self.__plotContainer(data, xValues, xLabel='Batch #', yLabel=fileName, title='{} over epochs'.format(fileName),
                                       labelFunc=lambda x: x)

            self.saveFigPDF([fig], fileName, self.saveFolder)

        for fileName in self.plotLayersSeparateKeys:
            data = self.containers[fileName]
            # build subplot for all plots
            nPlots = len(data)
            nRows, nCols = self.__findGrid(nPlots)
            fig, ax = plt.subplots(nrows=nRows, ncols=nCols)
            axRow, axCol = 0, 0
            figs = [fig]
            # add each layer alphas data to plot
            for i, layerData in enumerate(data):
                layerFig = self.__plotContainer(layerData, xValues, xLabel='Batch #', axOther=ax[axRow, axCol],
                                                title='{} --layer:[{}]-- over epochs'.format(fileName, i), yLabel=fileName,
                                                labelFunc=lambda x: self.layersBitwidths[i][x])
                figs.append(layerFig)
                # update next axes indices
                axCol = (axCol + 1) % nCols
                if axCol == 0:
                    axRow += 1

            # set fig properties
            self.__setFigProperties(fig, figSize=(40, 20))
            # save as HTML
            self.saveFigPDF(figs, fileName, self.saveFolder)

        # save plots data
        saveFile(self.plotsData, self.plotsDataFilePath)

    @staticmethod
    def plotBops(plotsData, bopsKey, baselineLabel, saveFolder):
        bopsData = plotsData[bopsKey]
        # bopsData['1'] = [(None, 0.86, 59.8), (None, 1.21, 61.3)]
        # bopsData['0'] = [(None, 0.88, 62.6), (None, 1.1, 61.6)]
        # create plots
        plots = [BopsStandardPlot('Accuracy vs. Bops', bopsData.keys()), BopsMaxAccuracyPlot('Max accuracy vs. Bops', bopsData.keys(), baselineLabel),
                 BopsMinBopsPlot('Accuracy vs. Min bops', bopsData.keys(), baselineLabel)]

        for i, (label, labelBopsData) in enumerate(bopsData.items()):
            for j, dataPoint in enumerate(labelBopsData):
                for plot in plots:
                    plot.addDataPoint(dataPoint, label)

            for plot in plots:
                plot.plot(label)

        # set plot properties
        for plot in plots:
            plot.setPlotProperties()

        # save as HTML
        Statistics.saveFigPDF([plot.fig for plot in plots], bopsKey, saveFolder)


class BopsPlot:
    def __init__(self, title, nKeys):
        self.title = title
        # create standard bops plot
        fig, ax = plt.subplots(nrows=1, ncols=1)
        ax.grid()

        self.fig = fig
        self.ax = ax

        # init colors
        colormap = plt.cm.hot
        self.colors = [colormap(i) for i in linspace(0.7, 0.0, len(nKeys))]
        self.nextColorIdx = 0

        # init yMax, yMin
        self.yMax = 0.0
        self.yMin = 100.0

        # init values
        self.xValues = []
        self.yValues = []

    def setPlotProperties(self):
        paddingPercentage = 0.02
        yMax = self.yMax * (1.0 + paddingPercentage)
        yMin = self.yMin * (1.0 - paddingPercentage)

        Statistics.setPlotProperties(self.fig, self.ax, xLabel='Bops / 1E9', yLabel='Accuracy', title=self.title, yMin=yMin, yMax=yMax)

    @abstractmethod
    def addDataPoint(self, dataPoint, label):
        raise NotImplementedError('subclasses must override addDataPoint()!')

    def addStandardDataPoint(self, dataPoint):
        bitwidth, bops, accuracy = dataPoint

        self.xValues.append(bops)
        self.yValues.append(accuracy)
        # update yMax, yMin
        self.yMax = max(self.yMax, accuracy)
        self.yMin = min(self.yMin, accuracy)
        # bitwidth might be None
        txt = '{:.3f}'.format(accuracy)
        if bitwidth:
            txt = '{},{}'.format(bitwidth, txt)
        # annotate
        self.ax.annotate(txt, (bops, accuracy), size=6)

    def __resetPlot(self):
        self.xValues.clear()
        self.yValues.clear()

    # plot label values
    def plot(self, label):
        self.ax.plot(self.xValues, self.yValues, 'o', label=label, c=self.colors[self.nextColorIdx])
        self.plotSpecific(label)
        self.nextColorIdx += 1
        self.__resetPlot()

    @abstractmethod
    def plotSpecific(self, label):
        raise NotImplementedError('subclasses must override plotSpecific()!')


class BopsStandardPlot(BopsPlot):
    def __init__(self, title, nKeys):
        super(BopsStandardPlot, self).__init__(title, nKeys)

    def addDataPoint(self, dataPoint, label):
        self.addStandardDataPoint(dataPoint)

    def plotSpecific(self, label):
        pass


class BopsPlotWithCondition(BopsPlot):
    def __init__(self, title, nKeys, baselineLabel):
        super(BopsPlotWithCondition, self).__init__(title, nKeys)

        self.baselineLabel = baselineLabel

    @abstractmethod
    def condition(self, dataPoint):
        raise NotImplementedError('subclasses must override condition()!')

    def addDataPoint(self, dataPoint, label):
        bitwidth, bops, accuracy = dataPoint

        if label == self.baselineLabel:
            self.addStandardDataPoint(dataPoint)

        elif self.condition(dataPoint):
            self.xValues = [bops]
            self.yValues = [accuracy]
            # update yMax, yMin
            self.yMax = max(self.yMax, accuracy)
            self.yMin = min(self.yMin, accuracy)

    def plotSpecific(self, label):
        if label != self.baselineLabel:
            accuracy = self.yValues[0]
            bops = self.xValues[0]

            txt = '{:.3f}'.format(accuracy)
            self.ax.annotate(txt, (bops, accuracy), size=6)


class BopsMaxAccuracyPlot(BopsPlotWithCondition):
    def __init__(self, title, nKeys, baselineLabel):
        super(BopsMaxAccuracyPlot, self).__init__(title, nKeys, baselineLabel)

    def condition(self, dataPoint):
        _, _, accuracy = dataPoint
        return len(self.yValues) == 0 or accuracy > self.yValues[0]


class BopsMinBopsPlot(BopsPlotWithCondition):
    def __init__(self, title, nKeys, baselineLabel):
        super(BopsMinBopsPlot, self).__init__(title, nKeys, baselineLabel)

    def condition(self, dataPoint):
        _, bops, _ = dataPoint
        return len(self.xValues) == 0 or bops < self.xValues[0]

# def plotBops(self, layersList):
#     # create plot
#     fig, ax = plt.subplots(nrows=1, ncols=1)
#     # init axis values
#     xValues = [[[] for _ in range(layer.numOfOps())] for layer in layersList]
#     yValues = [[[] for _ in range(layer.numOfOps())] for layer in layersList]
#     # init y axis max value
#     yMax = 0
#     for i, layer in enumerate(layersList):
#         for input_bitwidth in layer.bops.keys():
#             for j, bops in enumerate(layer.bops[input_bitwidth]):
#                 v = bops / 1E6
#                 xValues[i][j].append(i)
#                 yValues[i][j].append(v)
#                 ax.annotate('input:[{}]'.format(input_bitwidth), (i, v))
#                 yMax = max(yMax, v)
#
#     colors = {}
#     for i, (xLayerValues, yLayerValues) in enumerate(zip(xValues, yValues)):
#         for j, (x, y) in enumerate(zip(xLayerValues, yLayerValues)):
#             label = self.layersBitwidths[i][j]
#             if label in colors.keys():
#                 ax.plot(x, y, 'o', label=label, color=colors[label])
#             else:
#                 info = ax.plot(x, y, 'o', label=label)
#                 colors[label] = info[0].get_color()
#
#     yMax *= 1.1
#     self.__setPlotProperties(fig, ax, xLabel='Layer #', yLabel='M-bops', yMax=yMax, title='bops per op in layer')
#     # save as HTML
#     self.saveFigPDF([fig], fileName=self.bopsKey)
