# -*- coding: utf-8 -*-
"""
/***************************************************************************
Name                 : Catalog on the fly
Description          : Automatically adds  images that are in the catalog layer that intersect with the map area.
Date                 : April, 2015
copyright            : (C) 2015 by Luiz Motta
email                : motta.luiz@gmail.com

 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import urllib2
from datetime import datetime
from os.path import ( basename, dirname, sep as sepPath, isdir, join as joinPath )
from os import makedirs

import json

from PyQt4.QtCore import ( 
     Qt, QObject, QThread, QFileInfo, QDir, QVariant, QDate, QCoreApplication,
     QPyNullVariant, pyqtSignal, pyqtSlot
)
from PyQt4.QtGui  import (
     QAction,
     QApplication,  QCursor, QColor, QIcon,
     QTableWidget, QTableWidgetItem,
     QPushButton, QGridLayout, QProgressBar, QDockWidget, QWidget
)
from PyQt4.QtXml import QDomDocument

import qgis
from qgis.gui import ( QgsMessageBar ) 
from qgis.core import (
  QgsProject, QGis, QgsMessageLog,
  QgsMapLayerRegistry, QgsMapLayer,
  QgsFeature, QgsFeatureRequest, QgsGeometry, QgsRectangle,  QgsSpatialIndex,
  QgsCoordinateTransform,
  QgsRasterLayer, QgsRasterTransparency,
  QgsLayerTreeNode
)

from legendlayer import ( LegendRaster, LegendTMS )
from sortedlistbythread import SortedListByThread
from PyQt4.Qt import QDate

NAME_PLUGIN = "Catalog On The Fly"

class WorkerPopulateGroup(QObject):

  # Static
  TEMP_DIR = "/tmp"
  
  # Signals 
  finished = pyqtSignal( bool )
  messageStatus = pyqtSignal( str )
  messageError = pyqtSignal( str )

  def __init__(self, addLegendLayer):
    
    super(WorkerPopulateGroup, self).__init__()
    self.addLegendLayer = addLegendLayer

    self.sortedImages = SortedListByThread()
    self.killed = False
    self.canvas = qgis.utils.iface.mapCanvas()
    self.logMessage = QgsMessageLog.instance().logMessage
    self.nameFieldSource = self.layer = self.ltgCatalog = None

  def setData(self, data):
    self.nameFieldSource = data[ 'nameFieldSource' ]
    self.nameFieldDate = data[ 'nameFieldDate' ]
    self.layer = data[ 'layer' ]
    self.ltgCatalog = data[ 'ltgCatalog' ]

  @pyqtSlot()
  def run(self):

    def getImagesByCanvas():
      def getSourceDate(feat):
        return { 'source': feat[ self.nameFieldSource ], 'date': feat[ self.nameFieldDate ] } 

      def getSource(feat):
        return { 'source': feat[ self.nameFieldSource ] }

      images = []

      selectedImage = self.layer.selectedFeatureCount() > 0
      rectLayer = self.layer.extent() if not selectedImage else self.layer.boundingBoxOfSelected()
      crsLayer = self.layer.crs()

      crsCanvas = self.canvas.mapSettings().destinationCrs()
      ct = QgsCoordinateTransform( crsCanvas, crsLayer )
      rectCanvas = self.canvas.extent() if crsCanvas == crsLayer else ct.transform( self.canvas.extent() )

      if not rectLayer.intersects( rectCanvas ):
        return ( True, images ) 

      fr = QgsFeatureRequest()
      if selectedImage:
        fr.setFilterFids( self.layer.selectedFeaturesIds() )
      index = QgsSpatialIndex( self.layer.getFeatures( fr ) )
      fids = index.intersects( rectCanvas )
      del fr
      del index

      fr = QgsFeatureRequest()
      fr.setFilterFids ( fids )
      numImages = len( fids )
      del fids[:]
      it = self.layer.getFeatures( fr ) 
      f = QgsFeature()
      getF = getSourceDate if not self.nameFieldDate  is None else  getSource
      while it.nextFeature( f ):
        if f.geometry().intersects( rectCanvas ):
          images.append( getF( f ) )

      return ( True, images ) if len( images ) > 0 else ( False, numImages )

    def addImages():

      def finished(msg=None):
        if not msg is None:
          self.messageStatus.emit( msg )
        self.finished.emit( self.isKilled )

      def getFileInfo( image ):

        def prepareFileTMS( url_tms ):

          def createLocalFile():
            def populateLocalFile():
              html = response.read()
              response.close()
              fw = open( localName, 'w' )
              fw.write( html )
              fw.close()

            isOk = True
            try:
              response = urllib2.urlopen( url_tms )
            except urllib2.HTTPError, e:
              isOk = False
            except urllib2.URLError, e:
              isOk = False
            #
            if not isOk:
              return QFileInfo( url_tms ) # Add for error list
            else:
              populateLocalFile()
              return QFileInfo( localName )

          localName = "%s/%s" % ( self.TEMP_DIR, basename( url_tms ) )
          fileInfo = QFileInfo( localName )
          if not fileInfo.exists():
            fileInfo = createLocalFile()

          return fileInfo

        source = image[ 'source' ]
        isUrl = source.find('http://') == 0 or source.find('https://') == 0
        lenSource = len( source)
        isUrl = isUrl and source.rfind( 'xml', lenSource - len( 'xml' ) ) == lenSource - len( 'xml' )   
        fi = prepareFileTMS( source ) if isUrl else QFileInfo( source )

        dicReturn = { 'fileinfo': fi  }
        if image.has_key( 'date'):
          dicReturn['date'] = image['date']

        return dicReturn

      def getNameLayerDate(id):
        value = l_fileinfo[ id ]['date']
        vdate = value.toString( "yyyy-MM-dd" ) if type( value ) is QDate else value
        name = l_layer[ id ].name()
        return "%s (%s)" % ( vdate, name )

      def getNameLayer(id):
        return l_layer[ id ].name()

      def setTransparence():

        def getListTTVP():
          t = QgsRasterTransparency.TransparentThreeValuePixel()
          t.red = t.green = t.blue = 0.0
          t.percentTransparent = 100.0
          return [ t ]
        
        extension = ".xml"
        l_ttvp = getListTTVP()
        #
        for id in range( 0, len( l_raster ) ):
          fileName = l_fileinfo[ id ]['fileinfo'].fileName()
          idExt = fileName.rfind( extension )
          if idExt == -1 or len( fileName ) != ( idExt + len ( extension ) ):
            l_raster[ id ].renderer().rasterTransparency().setTransparentThreeValuePixelList( l_ttvp )

      def cleanLists( lsts ):
        for item in lsts:
          del item[:]
      
      # Sorted images
      key = 'date' if not self.nameFieldDate is None else 'source'
      f_key = lambda item: item[ key ]  
      l_image_sorted = self.sortedImages.run( images, f_key, True )
      if self.isKilled:
        del images[:]
        finished()
        return
      del images[:]

      l_fileinfo = map( getFileInfo, l_image_sorted )
      del l_image_sorted[:]

      l_raster = []
      l_error = []
      l_idRemove = []
      idRemove = 0
      for fi in l_fileinfo:
        layer = QgsRasterLayer( fi['fileinfo'].filePath(), fi['fileinfo'].baseName() )
        if layer.isValid():
          l_raster.append( layer )
        else:
          l_error.append( layer.source() )
          del layer
          l_idRemove.append( idRemove )
        idRemove += 1
        if self.isKilled:
          cleanLists( [ l_fileinfo, l_raster, l_error, l_idRemove ] )
          finished()
          return
      if len( l_idRemove ) > 0:
        l_idRemove.reverse()
        for id in l_idRemove:
          del l_fileinfo[ id ]
        del l_idRemove[:]
      # l_fileinfo, l_raster, l_error 

      totalRaster = len( l_raster ) 
      # Add raster
      if totalRaster > 0:
        setTransparence()
        l_layer = []
        for item in l_raster:
          if self.isKilled:
            break
          l_layer.append( QgsMapLayerRegistry.instance().addMapLayer( item, addToLegend=False ) )
        if self.isKilled:
          cleanLists( [ l_fileinfo, l_raster, l_error, l_layer ] )
          finished()
          return
        del l_raster[:]
        # l_fileinfo, l_error, l_layer
        getN = getNameLayer if self.nameFieldDate is None else getNameLayerDate
        for id in range( 0, len( l_layer ) ):
          ltl = self.ltgCatalog.addLayer( l_layer[ id ] )
          ltl.setVisible( Qt.Unchecked )
          name = getN( id )
          ltl.setLayerName( name )
          self.addLegendLayer( l_layer[ id ] )
        cleanLists( [ l_fileinfo, l_layer ] )
        # l_error

      # Message Error
      if len( l_error) > 0:
        for item in l_error:
          msgtrans = QCoreApplication.translate( "CatalogOTF", "Invalid image: %s" )
          msg = msgtrans % item
          self.logMessage( msg, "Catalog OTF", QgsMessageLog.CRITICAL )

        msgtrans = QCoreApplication.translate( "CatalogOTF", "Images invalids: %d. See log message" )
        msg = msgtrans % len( l_error ) 
        self.messageError.emit( msg )
        del l_error[:]

      finished( str( totalRaster ) )

    msgtrans = QCoreApplication.translate( "CatalogOTF", "Processing..." )
    self.messageStatus.emit( msgtrans )
    
    self.isKilled = False
    ( isOk, value ) = getImagesByCanvas()
    if self.isKilled:
      self.finished.emit( self.isKilled )
      return
    if not isOk:
      msgtrans = QCoreApplication.translate( "CatalogOTF", "Total of images(%d) exceeded the query limit." )
      msg = msgtrans % value 
      self.messageError.emit( msg )
      self.messageStatus.emit( "0" )
      self.finished.emit( self.isKilled )
      return

    images = value
    msgtrans = QCoreApplication.translate( "CatalogOTF", "Processing %d" )
    msg = msgtrans % len( images)
    self.messageStatus.emit( msg )
    addImages()

  def kill(self):
    self.isKilled = True
    self.sortedImages.kill()


class CatalogOTF(QObject):
  
  # Signals 
  settedLayer = pyqtSignal( "QgsVectorLayer")
  removedLayer = pyqtSignal( str )
  killed = pyqtSignal( str )
  changedNameLayer = pyqtSignal( str, str )
  changedTotal = pyqtSignal( str, str )
  changedIconRun = pyqtSignal( str, bool )

  def __init__(self, iface, tableCOTF):
    
    def connecTableCOTF():
      self.settedLayer.connect( tableCOTF.insertRow )
      self.removedLayer.connect( tableCOTF.removeRow )
      self.changedNameLayer.connect( tableCOTF.changedNameLayer )
      self.changedTotal.connect( tableCOTF.changedTotal )
      self.changedIconRun.connect( tableCOTF.changedIconRun )
      self.killed.connect( tableCOTF.killed )

    super(CatalogOTF, self).__init__()
    self.iface = iface
    self.canvas = iface.mapCanvas()
    self.ltv = iface.layerTreeView()
    self.model = self.ltv.layerTreeModel()
    self.ltgRoot = QgsProject.instance().layerTreeRoot()
    self.msgBar = iface.messageBar()
    self.legendTMS = LegendTMS( 'Catalog OTF' )
    self.legendRaster = LegendRaster( 'Catalog OTF' )

    self._initThread()

    connecTableCOTF()
    self.model.dataChanged.connect( self.dataChanged )
    QgsMapLayerRegistry.instance().layersWillBeRemoved.connect( self.layersWillBeRemoved ) # Catalog layer removed

    self.layer = self.layerName = self.nameFieldSource = self.nameFieldDate = None
    self.ltgCatalog = self.ltgCatalogName = self.visibleSourceLayers = self.hasCanceled = None

  def __del__(self):
    self._finishThread()
    del self.legendTMS
    del self.legendRaster
    QgsMapLayerRegistry.instance().layersWillBeRemoved.disconnect( self.layersWillBeRemoved ) # Catalog layer removed

  def _initThread(self):
    self.thread = QThread( self )
    self.thread.setObjectName( "QGIS_Plugin_%s" % NAME_PLUGIN.replace( ' ', '_' ) )
    self.worker = WorkerPopulateGroup( self.addLegendLayerWorker )
    self.worker.moveToThread( self.thread )
    self._connectWorker()

  def _finishThread(self):
    self._connectWorker( False )
    self.worker.deleteLater()
    self.thread.wait()
    self.thread.deleteLater()
    self.thread = self.worker = None

  def _connectWorker(self, isConnect = True):
    ss = [
      { 'signal': self.thread.started, 'slot': self.worker.run },
      { 'signal': self.worker.finished, 'slot': self.finishedPG },
      { 'signal': self.worker.messageStatus, 'slot': self.messageStatusPG },
      { 'signal': self.worker.messageError, 'slot': self.messageErrorPG }
    ]
    if isConnect:
      for item in ss:
        item['signal'].connect( item['slot'] )  
    else:
      for item in ss:
        item['signal'].disconnect( item['slot'] )

  def addLegendLayerWorker(self, layer):
    if layer.type() == QgsMapLayer.RasterLayer:  
      metadata = layer.metadata()
      if metadata.find( "GDAL provider" ) != -1:
        if  metadata.find( "OGC Web Map Service" ) != -1:
          if self.legendTMS.hasTargetWindows( layer ):
            self.legendTMS.setLayer( layer )
        else:
          self.legendRaster.setLayer( layer )

  def run(self):
    self.hasCanceled = False # Check in finishedPG

    if self.thread.isRunning():
      self.worker.kill()
      self.hasCanceled = True
      msgtrans = QCoreApplication.translate("CatalogOTF", "Canceled search for image from layer %s")
      msg = msgtrans % self.layerName  
      self.msgBar.pushMessage( NAME_PLUGIN, msg, QgsMessageBar.WARNING, 2 )
      self.changedTotal.emit( self.layer.id(), "Canceling processing")
      self.killed.emit( self.layer.id() )
      return

    if self.layer is None:
      msgtrans = QCoreApplication.translate("CatalogOTF", "Need define layer catalog")
      self.msgBar.pushMessage( NAME_PLUGIN, msgtrans, QgsMessageBar.WARNING, 2 )
      return

    self._setGroupCatalog()
    self.ltgCatalogName = self.ltgCatalog.name()

    renderFlag = self.canvas.renderFlag()
    if renderFlag:
      self.canvas.setRenderFlag( False )
      self.canvas.stopRendering()

    self._populateGroupCatalog()

    if renderFlag:
      self.canvas.setRenderFlag( True )
      self.canvas.refresh()

  def _populateGroupCatalog(self):

    def getSourceVisibleLayers():
      def hasVisibleRaster( ltl ):
        return ltl.isVisible() == Qt.Checked and ltl.layer().type() == QgsMapLayer.RasterLayer
      l_ltlVisible = filter( lambda item: hasVisibleRaster( item ), self.ltgCatalog.findLayers() )
      return map( lambda item: item.layer().source(),  l_ltlVisible )

    def runWorker():
      data = {}
      data['nameFieldDate'] = self.nameFieldDate
      data['nameFieldSource'] = self.nameFieldSource
      data['layer'] = self.layer
      data['ltgCatalog'] = self.ltgCatalog
      self.worker.setData( data )
      self.thread.start()
      #self.worker.run() # DEBUG

    self.visibleSourceLayers = getSourceVisibleLayers()
    self.ltgCatalog.removeAllChildren()
    runWorker() # See finishPG

  def _setGroupCatalog(self):
    self.ltgCatalogName = "%s - Catalog" % self.layer.name()
    self.ltgCatalog = self.ltgRoot.findGroup( self.ltgCatalogName  )
    if self.ltgCatalog is None:
      self.ltgCatalog = self.ltgRoot.addGroup( self.ltgCatalogName )

  @pyqtSlot( bool )
  def finishedPG(self, isKilled ):
    def setSourceVisibleLayers():
      l_ltlVisible = filter( lambda item: item.layer().source() in self.visibleSourceLayers, self.ltgCatalog.findLayers() )
      map( lambda item: item.setVisible( Qt.Checked ),  l_ltlVisible )

    self.thread.quit()
    
    if not self.layer is None:
      self.changedIconRun.emit( self.layer.id(), self.layer.selectedFeatureCount() > 0 )
      if self.hasCanceled:
        self.changedTotal.emit( self.layer.id(), '0')
      else:
        setSourceVisibleLayers()

    del self.visibleSourceLayers[:]

  @pyqtSlot( str )
  def messageStatusPG(self, msg):
    self.changedTotal.emit( self.layer.id(), msg  )

  @pyqtSlot( str )
  def messageErrorPG(self, msg):
    self.msgBar.pushMessage( NAME_PLUGIN, msg, QgsMessageBar.CRITICAL, 8 )

  @pyqtSlot( 'QModelIndex', 'QModelIndex' )
  def dataChanged(self, idTL, idBR):
    if idTL != idBR:
      return

    if not self.ltgCatalog is None and self.ltgCatalog == self.model.index2node( idBR ):
      name = self.ltgCatalog.name()
      if self.ltgCatalogName != name:
        self.ltgCatalogName = name
        return

    if not self.layer is None and self.ltgRoot.findLayer( self.layer.id() ) == self.model.index2node( idBR ):
      name = self.layer.name()
      if self.layerName != name:
        self.changedNameLayer.emit( self.layer.id(), name )
        self.layerName = name

  @pyqtSlot( list )
  def layersWillBeRemoved(self, layerIds):
    if self.layer is None:
      return
    if self.layer.id() in layerIds:
      self.removedLayer.emit( self.layer.id() )
      self.removeLayerCatalog()

  @staticmethod
  def getNameFieldsCatalog(layer):

    def getFirstFeature():
      f = QgsFeature()
      #
      fr = QgsFeatureRequest() # First FID can be 0 or 1 depend of provider type
      it = layer.getFeatures( fr )
      isOk = it.nextFeature( f )
      it.close()
      #
      if not isOk or not f.isValid():
        del f
        return None
      else:
        return f

    def hasAddress(feature, nameField):

      def asValidUrl( url):
        isOk = True
        try:
          urllib2.urlopen(url)
        except urllib2.HTTPError, e:
          isOk = False
        except urllib2.URLError, e:
          isOk = False
        #
        return isOk  

      value = feature.attribute( nameField )
      if value is None or type(value) == QPyNullVariant:
        return False

      isUrl = value.find('http://') == 0 or value.find('https://') == 0
      lenSource = len( value )
      isUrl = isUrl and value.rfind( 'xml', lenSource - len( 'xml' ) ) == lenSource - len( 'xml' )   
      if isUrl:
        return asValidUrl( value )
      #
      fileInfo = QFileInfo( value )
      return fileInfo.isFile()

    def hasDate(feature, nameField):
      value = feature.attribute( nameField )
      if value is None or type(value) == QPyNullVariant:
        return False
      
      date = value if type( value) is QDate else QDate.fromString( value, 'yyyy-MM-dd' )
                
      return True if date.isValid() else False

    if layer is None or layer.type() != QgsMapLayer.VectorLayer or layer.geometryType() != QGis.Polygon:
      return None

    firstFeature = getFirstFeature()
    if firstFeature is None:
      return None

    fieldSource = None
    fieldDate = None
    isOk = False
    for item in layer.pendingFields().toList():
      nameField = item.name()
      if item.type() == QVariant.String:
        if fieldSource is None and hasAddress( firstFeature, nameField ):
          fieldSource = nameField
        elif fieldDate is None and hasDate( firstFeature, nameField ):
          fieldDate = nameField
      elif item.type() == QVariant.Date:
        if fieldDate is None and hasDate( firstFeature, nameField ):
          fieldDate = nameField
    if not fieldSource is None:
      isOk = True

    return { 'nameSource': fieldSource, 'nameDate': fieldDate } if isOk else None 

  def setLayerCatalog(self, layer, nameFiedlsCatalog):
    self.layer = layer
    self.layerName = layer.name()
    self.nameFieldSource = nameFiedlsCatalog[ 'nameSource' ]
    self.nameFieldDate = nameFiedlsCatalog[ 'nameDate' ]
    self.settedLayer.emit( self.layer )

  def removeLayerCatalog(self):
    self.ltgRoot.removeChildNode( self.ltgCatalog )
    self.ltgCatalog = None
    self.layer = self.nameFieldSource = self.nameFieldDate =  None


class TableCatalogOTF(QObject):

  runCatalog = pyqtSignal( str )

  def __init__(self):
    def initGui():
      self.tableWidget.setWindowTitle("Catalog OTF")
      self.tableWidget.setSortingEnabled( False )
      msgtrans = QCoreApplication.translate("CatalogOTF", "Layer,Total")
      headers = msgtrans.split(',')
      self.tableWidget.setColumnCount( len( headers ) )
      self.tableWidget.setHorizontalHeaderLabels( headers )
      self.tableWidget.resizeColumnsToContents()

    super( TableCatalogOTF, self ).__init__()
    self.tableWidget = QTableWidget()
    initGui()

  def _getRowLayerID(self, layerID):
    for row in range( self.tableWidget.rowCount() ):
      if layerID == self.tableWidget.cellWidget( row, 0 ).objectName():
        return row
    return -1

  def _changedText(self, layerID, name, column):
    row = self._getRowLayerID( layerID )
    if row != -1:
      wgt = self.tableWidget.cellWidget( row, column ) if column == 0 else self.tableWidget.item( row, column )
      wgt.setText( name )
      wgt.setToolTip( name )
      self.tableWidget.resizeColumnsToContents()

  @pyqtSlot()
  def _onRunCatalog(self):
    btn = self.sender()
    icon = QIcon( joinPath( dirname(__file__), 'cancel_red.svg' ) )
    btn.setIcon( icon )
    layerID = btn.objectName() 
    self.runCatalog.emit( layerID )

  @pyqtSlot()  
  def _onSelectionChanged(self):
    layer = self.sender()
    row = self._getRowLayerID( layer.id() )
    if row != -1:
      wgt = self.tableWidget.cellWidget( row, 0 )
      nameIcon = 'check_green.svg' if layer.selectedFeatureCount() == 0 else 'check_yellow.svg'
      icon = QIcon( joinPath( dirname(__file__), nameIcon ) )
      wgt.setIcon( icon )

  @pyqtSlot( "QgsVectorLayer")
  def insertRow(self, layer):
    row = self.tableWidget.rowCount()
    self.tableWidget.insertRow( row )

    column = 0 # Layer
    layerName = layer.name()
    nameIcon = 'check_green.svg' if layer.selectedFeatureCount() == 0 else 'check_yellow.svg'
    icon = QIcon( joinPath( dirname(__file__), nameIcon ) )
    btn = QPushButton( icon, layerName, self.tableWidget )
    btn.setObjectName( layer.id() )
    btn.setToolTip( layerName )
    btn.clicked.connect( self._onRunCatalog )
    layer.selectionChanged.connect( self._onSelectionChanged )
    self.tableWidget.setCellWidget( row, column, btn )

    column = 1 # Total

    msgtrans = QCoreApplication.translate("CatalogOTF", "None")
    item = QTableWidgetItem( msgtrans )
    item.setFlags( Qt.ItemIsSelectable | Qt.ItemIsEnabled )
    self.tableWidget.setItem( row, column, item )

    self.tableWidget.resizeColumnsToContents()

  @pyqtSlot( str )
  def removeRow(self, layerID):
    row = self._getRowLayerID( layerID )
    if row != -1:
      self.tableWidget.removeRow( row )

  @pyqtSlot( str, str )
  def changedNameLayer(self, layerID, name):
    self._changedText( layerID, name, 0 )

  @pyqtSlot( str, str )
  def changedTotal(self, layerID, value):
    self._changedText( layerID, value, 1 )

  @pyqtSlot( str, bool )
  def changedIconRun(self, layerID, selected):
    row = self._getRowLayerID( layerID )
    if row != -1:
      btn = self.tableWidget.cellWidget( row, 0 )
      nameIcon = 'check_green.svg' if not selected else 'check_yellow.svg'
      icon = QIcon( joinPath( dirname(__file__), nameIcon ) )
      btn.setIcon( icon )
      btn.setEnabled( True )

  @pyqtSlot( str )
  def killed(self, layerID):
    row = self._getRowLayerID( layerID )
    if row != -1:
      btn = self.tableWidget.cellWidget( row, 0 )
      btn.setEnabled( False )

  def widget(self):
    return self.tableWidget


class DockWidgetCatalogOTF(QDockWidget):

  def __init__(self, iface):

    def setupUi():
      self.setObjectName( "catalogotf_dockwidget" )
      wgt = QWidget( self )
      wgt.setAttribute(Qt.WA_DeleteOnClose)
      #
      gridLayout = QGridLayout( wgt )
      gridLayout.setContentsMargins( 0, 0, gridLayout.verticalSpacing(), gridLayout.verticalSpacing() )
      #
      tbl = self.tbl_cotf.widget()
      ( iniY, iniX, spanY, spanX ) = ( 0, 0, 1, 2 )
      gridLayout.addWidget( tbl, iniY, iniX, spanY, spanX )
      #
      msgtrans = QCoreApplication.translate("CatalogOTF", "Find catalog")
      btnFindCatalogs = QPushButton( msgtrans, wgt )
      btnFindCatalogs.clicked.connect( self.findCatalogs )
      ( iniY, iniX, spanY, spanX ) = ( 1, 0, 1, 1 )
      gridLayout.addWidget( btnFindCatalogs, iniY, iniX, spanY, spanX )
      #
      wgt.setLayout( gridLayout )
      self.setWidget( wgt )

    super( DockWidgetCatalogOTF, self ).__init__( "Catalog On The Fly", iface.mainWindow() )
    #
    self.iface = iface
    self.cotf = {} 
    self.tbl_cotf = TableCatalogOTF()
    self.tbl_cotf.runCatalog.connect( self._onRunCatalog )
    #
    setupUi()

  @pyqtSlot( str )
  def _onRunCatalog(self, layerID):
    if layerID in self.cotf.keys(): # Maybe Never happend
      self.cotf[ layerID ].run()
  
  @pyqtSlot( str )
  def removeLayer(self, layerID):
    self.cotf[ layerID ].worker.kill()
    del self.cotf[ layerID ]

  @pyqtSlot()
  def findCatalogs(self):
    def addLegendImages(layer):
     name = "%s - Catalog" % layer.name()
     ltgCatalog = QgsProject.instance().layerTreeRoot().findGroup( name  )
     if not ltgCatalog is None:
      for item in map( lambda item: item.layer(), ltgCatalog.findLayers() ):
        self.cotf[ layerID ].addLegendLayerWorker( item )

    def checkTempDir():
      tempDir = QDir( WorkerPopulateGroup.TEMP_DIR )
      if not tempDir.exists():
        msgtrans1 = QCoreApplication.translate("CatalogOTF", "Created temporary directory '%s' for GDAL_WMS")
        msgtrans2 = QCoreApplication.translate("CatalogOTF", "Not possible create temporary directory '%s' for GDAL_WMS")
        isOk = tempDir.mkpath( WorkerPopulateGroup.TEMP_DIR )
        msgtrans = msgtrans1 if isOk else msgtrans2
        tempDir.setPath( WorkerPopulateGroup.TEMP_DIR )
        msg = msgtrans % tempDir.absolutePath()
        msgBar.pushMessage( NAME_PLUGIN, msg, QpluginNamegsMessageBar.CRITICAL, 5 )

    def overrideCursor():
      cursor = QApplication.overrideCursor()
      if cursor is None or cursor == 0:
          QApplication.setOverrideCursor( QCursor( Qt.WaitCursor ) )
      elif cursor.shape() != Qt.WaitCursor:
          QApplication.setOverrideCursor( QCursor( Qt.WaitCursor ) )

    overrideCursor()
    find = False
    f = lambda item: \
        item.type() == QgsMapLayer.VectorLayer and \
        item.geometryType() == QGis.Polygon and \
        not item.id() in self.cotf.keys()
    for item in filter( f, self.iface.legendInterface().layers() ):
      nameFiedlsCatalog = CatalogOTF.getNameFieldsCatalog( item )
      if not nameFiedlsCatalog is None:
        layerID = item.id()
        self.cotf[ layerID ] = CatalogOTF( self.iface, self.tbl_cotf )
        self.cotf[ layerID ].removedLayer.connect( self.removeLayer )
        self.cotf[ layerID ].setLayerCatalog( item, nameFiedlsCatalog ) # Insert table
        addLegendImages( item )
        find = True
    #
    msgBar = self.iface.messageBar()
    if not find:
      f = lambda item: \
          item.type() == QgsMapLayer.VectorLayer and \
          item.geometryType() == QGis.Polygon
      totalLayers = len( filter( f, self.iface.legendInterface().layers() ) )
      msgtrans = QCoreApplication.translate("CatalogOTF", "Did not find a new catalog. Catalog layers %d of %d(polygon layers)")
      msg = msgtrans % ( len( self.cotf ), totalLayers ) 
      msgBar.pushMessage( NAME_PLUGIN, msg, QgsMessageBar.INFO, 3 )
    else:
      checkTempDir()

    QApplication.restoreOverrideCursor()


class ProjectDockWidgetCatalogOTF():

  pluginName = "Plugin_DockWidget_Catalog_OTF"
  pluginSetting = "/images_wms"
  nameTmpDir = "tmp"

  def __init__(self, iface):
    self.iface = iface

  @pyqtSlot("QDomDocument")
  def onReadProject(self, document):
    def createTmpDir():
      tmpDir = "%s%s" % ( sepPath, self.nameTmpDir )
      if not isdir( tmpDir ):
        makedirs( tmpDir )

    proj = QgsProject.instance()
    value, ok = proj.readEntry( self.pluginName, self.pluginSetting )
    if ok and bool( value ):
      createTmpDir()
      newImages = 0
      for item in json.loads( value ):
        source = item['source']
        if not QFileInfo( source ).exists():
          fw = open( source, 'w' )
          fw.write( item[ 'wms' ] )
          fw.close()
          newImages += 1
      if newImages > 0:
        msgtrans = QCoreApplication.translate( "CatalogOTF", "Please reopen project - DON'T SAVE. The GDAL_WMS images were regenerated (%d images)" )
        msg = msgtrans % newImages
        self.iface.messageBar().pushMessage( NAME_PLUGIN, msg, QgsMessageBar.WARNING, 8 )

  @pyqtSlot("QDomDocument")
  def onWriteProject(self, document):
    def getContentFile( source ):
      with open( source, 'r' ) as content_file:
        content = content_file.read()
      return content

    def filter_wms_tmp( layer ):
      if not layer.type() == QgsMapLayer.RasterLayer:
        return False

      metadata = layer.metadata()
      if not ( metadata.find( "GDAL provider" ) != -1 and metadata.find( "OGC Web Map Service" ) != -1  ):
        return False

      lstDir = dirname( layer.source() ).split( sepPath)
      if not ( len( lstDir) == 2 and lstDir[1] == self.nameTmpDir ):
        return False
      
      return True

    layers = map ( lambda item: item.layer(), self.iface.layerTreeView().layerTreeModel().rootGroup().findLayers() )
    layers_wms_tmp = filter( filter_wms_tmp, layers )
    images_wms = []
    for item in layers_wms_tmp:
      source = item.source()
      images_wms.append( { 'source': source, 'wms': getContentFile( source) } )
    proj = QgsProject.instance()
    if len( images_wms ) == 0:
      proj.removeEntry( self.pluginName, self.pluginSetting )
    else:
      proj.writeEntry( self.pluginName, self.pluginSetting, json.dumps( images_wms ) )
