# -*- coding: utf-8 -*-
"""Topology Inspector v1.4 for Autodesk Maya (Python 3 / Viewport 2.0)

A read-only mesh topology inspector. The only scene node created is a temporary
custom locator used to draw colored polygons and thick boundaries. Original
geometry, vertex colors, shading assignments, and history are untouched.

Drop this .py onto a Maya viewport to install it automatically and create a
Topology Inspector button in the Custom Shelf. The tool also opens on install.

To reopen later, click the TI shelf icon. Existing v1.1/v1.2 plug-ins do not need to be unloaded.
The user shelf icon and script are installed into Maya's preferences/scripts.
"""

import re
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import maya.api.OpenMayaRender as omr


def maya_useNewAPI():
    """Tell Maya to pass API 2.0 objects to plugin entry points."""
    pass


PLUGIN_NODE = 'tiViewportOverlayShapeV14'
PLUGIN_COMMAND = 'topologyInspectorV14UI'
NODE_ID = om.MTypeId(0x0013B9D6)  # If this id conflicts with another plugin, change it.
CLASSIFICATION = 'drawdb/geometry/tiViewportOverlayShapeV14'
REGISTRANT_ID = 'TopologyInspectorVP2RegistrantV14'
WINDOW_NAME = 'TopologyInspectorWindowV14'

# Draw data is shared by the tool and VP2 override within the same plug-in module.
_GEOM = {}
_UI = None
_VIEW = {
    'line_width': 4.0,
    'opacity': 0.40,
    'visible': True,
    'errors_visible': False,
}


def _blank_geom():
    return {
        'tri_fill': om.MPointArray(),
        'tri_lines': om.MPointArray(),
        'ngon_fill': om.MPointArray(),
        'ngon_lines': om.MPointArray(),
        'error_fill': om.MPointArray(),
        'error_lines': om.MPointArray(),
        'error_points': om.MPointArray(),
    }


def _replace_geom(new_data=None):
    global _GEOM
    _GEOM = new_data if new_data is not None else _blank_geom()
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


_replace_geom()


class TIOverlayNode(omui.MPxLocatorNode):
    @staticmethod
    def creator():
        return TIOverlayNode()

    @staticmethod
    def initialize():
        pass

    def isBounded(self):
        return False

    def excludeAsLocator(self):
        # Remain visible when the regular Viewport > Show > Locators is off.
        return False


class TIOverlayDraw(omr.MPxDrawOverride):
    @staticmethod
    def creator(obj):
        return TIOverlayDraw(obj)

    def __init__(self, obj):
        super(TIOverlayDraw, self).__init__(obj, None, True)

    def supportedDrawAPIs(self):
        return (omr.MRenderer.kOpenGLCoreProfile |
                omr.MRenderer.kOpenGL |
                omr.MRenderer.kDirectX11)

    def isBounded(self, objPath, cameraPath):
        return False

    def hasUIDrawables(self):
        return True

    def isTransparent(self):
        return True

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        # No Maya geometry is generated, so all drawing is temporary.
        return None

    def addUIDrawables(self, objPath, draw, frameContext, data):
        if not _VIEW['visible']:
            return
        draw.beginDrawable(omr.MUIDrawManager.kNonSelectable)
        try:
            draw.setPaintStyle(omr.MUIDrawManager.kFlat)
            a = _VIEW['opacity']
            width = _VIEW['line_width']

            def fill(key, color):
                pts = _GEOM[key]
                if len(pts):
                    draw.setColor(om.MColor((color[0], color[1], color[2], a)))
                    draw.mesh(omr.MUIDrawManager.kTriangles, pts)

            def line(key, color, thickness=width):
                pts = _GEOM[key]
                if len(pts):
                    draw.setColor(om.MColor((color[0], color[1], color[2], 1.0)))
                    draw.setLineWidth(float(thickness))
                    draw.mesh(omr.MUIDrawManager.kLines, pts)

            # Fill first and draw borders afterward so outlines stay crisp.
            fill('ngon_fill', (1.0, 0.08, 0.08))
            fill('tri_fill', (0.10, 0.35, 1.0))
            line('ngon_lines', (1.0, 0.02, 0.02))
            line('tri_lines', (0.05, 0.45, 1.0))

            if _VIEW['errors_visible']:
                fill('error_fill', (1.0, 0.88, 0.0))
                line('error_lines', (1.0, 0.98, 0.0), max(4.0, width + 1.0))
                points = _GEOM['error_points']
                if len(points):
                    draw.setColor(om.MColor((1.0, 0.95, 0.0, 1.0)))
                    draw.setPointSize(max(8.0, width * 2.0))
                    draw.mesh(omr.MUIDrawManager.kPoints, points)
        finally:
            draw.endDrawable()


def _extract_indices(lines, component):
    """Parse Maya's polyInfo component strings without altering selection."""
    if not lines:
        return set()
    # Handles full DAG paths, including the case where several tokens share a line.
    pattern = re.compile(r'\.' + re.escape(component) + r'\[(\d+)\]')
    return {int(x) for entry in lines for x in pattern.findall(entry)}


def _get_selected_mesh():
    """Return (transform fullPath, shape fullPath), or (None, None)."""
    # Keep component selections: objectsOnly=True discards .f/.e/.vtx picks.
    selected = cmds.ls(selection=True, long=True) or []
    for entry in reversed(selected):
        item = entry.split(".", 1)[0]
        if not cmds.objExists(item):
            continue
        if cmds.nodeType(item) == 'mesh':
            shapes = [item]
            parent = cmds.listRelatives(item, parent=True, fullPath=True) or []
            transform = parent[0] if parent else item
        elif cmds.nodeType(item) == 'transform':
            transform = item
            shapes = cmds.listRelatives(item, shapes=True, noIntermediate=True,
                                        fullPath=True, type='mesh') or []
        else:
            continue
        if not shapes:
            continue
        return transform, shapes[0]
    return None, None


def _mesh_api(shape):
    sel = om.MSelectionList()
    sel.add(shape)
    path = sel.getDagPath(0)
    return path, om.MFnMesh(path)


def _calc_nonmanifold(shape, path, mesh_fn):
    """Find problematic topological elements AND their neighboring geometry."""
    edge_ids = _extract_indices(cmds.polyInfo(shape, nonManifoldEdges=True), 'e')
    vert_ids = _extract_indices(cmds.polyInfo(shape, nonManifoldVertices=True), 'vtx')
    lamina_ids = _extract_indices(cmds.polyInfo(shape, laminaFaces=True), 'f')

    face_ids = set(lamina_ids)
    related_edges = set(edge_ids)
    edge_it = om.MItMeshEdge(path)
    vert_it = om.MItMeshVertex(path)
    face_it = om.MItMeshPolygon(path)

    for edge in sorted(edge_ids):
        edge_it.setIndex(edge)
        face_ids.update(int(x) for x in edge_it.getConnectedFaces())

    for vertex in sorted(vert_ids):
        vert_it.setIndex(vertex)
        related_edges.update(int(x) for x in vert_it.getConnectedEdges())
        face_ids.update(int(x) for x in vert_it.getConnectedFaces())

    for face in sorted(lamina_ids):
        face_it.setIndex(face)
        related_edges.update(int(x) for x in face_it.getEdges())

    return {
        'nonmanifold_edges': edge_ids,
        'nonmanifold_vertices': vert_ids,
        'lamina_faces': lamina_ids,
        'related_faces': face_ids,
        'related_edges': related_edges,
    }


def _collect_geometry(shape):
    """Classify original polygon faces and build temporary VP2 line/face buffers."""
    path, mesh_fn = _mesh_api(shape)
    points = mesh_fn.getPoints(om.MSpace.kWorld)
    counts, polygon_vertex_ids = mesh_fn.getVertices()

    triangles = []
    ngons = []
    face_vertices = {}
    cursor = 0
    for face_id, count in enumerate(counts):
        vs = tuple(int(v) for v in polygon_vertex_ids[cursor:cursor + count])
        cursor += count
        if count == 3:
            triangles.append(face_id)
            face_vertices[face_id] = vs
        elif count > 4:
            ngons.append(face_id)
            face_vertices[face_id] = vs

    errors = _calc_nonmanifold(shape, path, mesh_fn)
    error_faces = errors['related_faces']
    all_interesting = set(triangles) | set(ngons) | error_faces
    geom = _blank_geom()

    # Get Maya's OWN triangulation, rather than making an incorrect fan for
    # concave N-gons. Buffer contains only actual rendered face triangles.
    face_triangles = {}
    try:
        tri_counts, all_tri_vertices = mesh_fn.getTriangles()
        offset = 0
        for face_id, tri_count in enumerate(tri_counts):
            if face_id in all_interesting:
                begin = offset * 3
                end = (offset + tri_count) * 3
                face_triangles[face_id] = all_tri_vertices[begin:end]
            offset += tri_count
    except Exception:
        # Invalid geometry may be untriangulatable. Still draw borders and
        # make it selectable; do not silently substitute a wrong surface.
        pass

    def add_fill(face_id, array):
        for vert_id in face_triangles.get(face_id, []):
            array.append(points[int(vert_id)])

    for face_id in triangles:
        add_fill(face_id, geom['tri_fill'])
    for face_id in ngons:
        add_fill(face_id, geom['ngon_fill'])
    for face_id in sorted(error_faces):
        add_fill(face_id, geom['error_fill'])

    def add_face_edges(face_ids, array):
        # Avoid duplicate edges at shared borders of the same category.
        seen = set()
        for face_id in face_ids:
            vs = face_vertices.get(face_id)
            if vs is None:
                vs = mesh_fn.getPolygonVertices(face_id)
            for i in range(len(vs)):
                x, y = int(vs[i]), int(vs[(i + 1) % len(vs)])
                key = (min(x, y), max(x, y))
                if key in seen:
                    continue
                seen.add(key)
                array.append(points[x])
                array.append(points[y])

    add_face_edges(ngons, geom['ngon_lines'])
    add_face_edges(triangles, geom['tri_lines'])

    # Highlight the exact detected edges and edges incident to bad vertices,
    # rather than outlining every face of the neighborhood yellow.
    for edge_id in sorted(errors['related_edges']):
        a, b = mesh_fn.getEdgeVertices(edge_id)
        geom['error_lines'].append(points[a])
        geom['error_lines'].append(points[b])
    for vertex_id in sorted(errors['nonmanifold_vertices']):
        geom['error_points'].append(points[vertex_id])

    result = {
        'triangles': triangles,
        'ngons': ngons,
        'errors': errors,
        'geom': geom,
    }
    return result


class TopologyInspectorWindow(object):
    def __init__(self):
        self.transform = None
        self.shape = None
        self.target_transform = None
        self.target_shape = None
        self.result = None
        self.status = None
        self.stats = None
        self._closing = False
        self._suppress_selection = False
        self._scene_callbacks = []

        if cmds.window(WINDOW_NAME, exists=True):
            cmds.deleteUI(WINDOW_NAME)

        self.window = cmds.window(
            WINDOW_NAME, title='Topology Inspector  |  Tri / N-gon / Non-Manifold',
            sizeable=False, widthHeight=(360, 420),
            closeCommand=self.close,
        )
        cmds.columnLayout(adjustableColumn=True, rowSpacing=7, columnAttach=('both', 12))
        cmds.text(label='TOPOLOGY INSPECTOR', height=28,
                  font='boldLabelFont', align='left')
        cmds.text(label='Select a polygon mesh to inspect it automatically.', align='left')
        cmds.separator(style='in', height=10)
        self.stats = cmds.text(label='No polygon mesh selected', align='left', height=42)

        cmds.button(label='SELECT ALL N-GONS  (5+ sides)', height=38,
                    backgroundColor=(0.64, 0.17, 0.17),
                    command=lambda *_: self.select_category('ngon'))
        cmds.button(label='SELECT ALL TRIANGLES  (3 sides)', height=38,
                    backgroundColor=(0.16, 0.32, 0.70),
                    command=lambda *_: self.select_category('tri'))
        cmds.button(label='SELECT BOTH  (TRIS + N-GONS)', height=37,
                    backgroundColor=(0.36, 0.24, 0.53),
                    command=lambda *_: self.select_category('both'))
        cmds.button(label='FIND NON-MANIFOLD + LAMINA', height=41,
                    backgroundColor=(0.70, 0.61, 0.13),
                    command=lambda *_: self.select_category('bad'))
        cmds.separator(style='in', height=12)
        cmds.checkBox(label='Show viewport color overlay', value=True,
                      changeCommand=self._set_visibility)
        cmds.floatSliderGrp(label='Edge width', field=True, minValue=1.0,
                            maxValue=9.0, fieldMinValue=1.0, fieldMaxValue=20.0,
                            value=4.0, precision=1,
                            changeCommand=self._set_width)
        cmds.floatSliderGrp(label='Face opacity', field=True, minValue=0.08,
                            maxValue=0.85, fieldMinValue=0.0, fieldMaxValue=1.0,
                            value=0.4, precision=2,
                            changeCommand=self._set_opacity)
        cmds.button(label='REFRESH SELECTED MESH / CLEAR YELLOW', height=30,
                    command=lambda *_: self.refresh(reset_yellow=True))
        self.status = cmds.text(label='Red: N-gon  |  Blue: Triangle  |  Yellow: bad topology',
                                align='left', height=30)
        cmds.showWindow(self.window)

        self._ensure_overlay()
        cmds.scriptJob(event=['SelectionChanged', self.on_selection_changed],
                       parent=self.window, protected=True)
        cmds.scriptJob(event=['Undo', self.on_scene_changed],
                       parent=self.window, protected=True)
        cmds.scriptJob(event=['Redo', self.on_scene_changed],
                       parent=self.window, protected=True)
        cmds.scriptJob(event=['timeChanged', self.on_scene_changed],
                       parent=self.window, protected=True)
        # These are MSceneMessage notifications, NOT scriptJob events.
        # Keeping the overlay out of saved scenes avoids unknown plugin nodes.
        self._scene_callbacks.append(
            om.MSceneMessage.addCallback(om.MSceneMessage.kBeforeSave,
                                         self._before_save))
        self._scene_callbacks.append(
            om.MSceneMessage.addCallback(om.MSceneMessage.kAfterSave,
                                         self._after_save))
        cmds.scriptJob(event=['NewSceneOpened', self.on_scene_changed],
                       parent=self.window, protected=True)
        cmds.scriptJob(event=['SceneOpened', self.on_scene_changed],
                       parent=self.window, protected=True)
        self.on_selection_changed()

    def _ensure_overlay(self):
        if self.transform and cmds.objExists(self.transform):
            return
        self.transform = cmds.createNode('transform', name='__TI_ViewportOverlay_TEMP_V14__')
        self.shape = cmds.createNode(PLUGIN_NODE, parent=self.transform,
                                     name='__TI_ViewportOverlayShape_TEMP_V14__')
        # Does not obstruct selection in the viewport.
        cmds.setAttr(self.transform + '.hiddenInOutliner', True)
        try:
            cmds.setAttr(self.shape + '.hiddenInOutliner', True)
        except Exception:
            pass
        cmds.setAttr(self.transform + '.inheritsTransform', False)
        cmds.setAttr(self.transform + '.visibility', bool(_VIEW['visible']))

    def _remove_overlay(self):
        if self.transform and cmds.objExists(self.transform):
            cmds.delete(self.transform)
        self.transform = None
        self.shape = None

    def _before_save(self, *args):
        # Never leave the custom plugin node serialized inside Maya scene files.
        self._remove_overlay()

    def _after_save(self, *args):
        if not self._closing:
            self._ensure_overlay()
            cmds.refresh(force=True)

    def _set_visibility(self, val):
        _VIEW['visible'] = bool(val)
        if self.transform and cmds.objExists(self.transform):
            cmds.setAttr(self.transform + '.visibility', bool(val))
        cmds.refresh(force=True)

    def _set_width(self, val):
        _VIEW['line_width'] = float(val)
        cmds.refresh(force=True)

    def _set_opacity(self, val):
        _VIEW['opacity'] = float(val)
        cmds.refresh(force=True)

    def on_scene_changed(self, *_):
        if self._closing:
            return
        self.target_shape = None
        self.target_transform = None
        self.result = None
        self._ensure_overlay()
        self.on_selection_changed()

    def on_selection_changed(self, *_):
        if self._closing or self._suppress_selection:
            return
        transform, shape = _get_selected_mesh()
        if not shape:
            # Selection cleared or switched to a nonmesh node.
            self.target_shape = None
            self.target_transform = None
            self.result = None
            _VIEW['errors_visible'] = False
            _replace_geom()
            cmds.text(self.stats, edit=True, label='No polygon mesh selected')
            return
        # Component selections on the same mesh don't trigger a fresh scan.
        if shape == self.target_shape and self.result is not None:
            return
        self.target_transform = transform
        self.target_shape = shape
        self.refresh(reset_yellow=True)

    def refresh(self, reset_yellow=False):
        if reset_yellow:
            _VIEW['errors_visible'] = False
        if not self.target_shape or not cmds.objExists(self.target_shape):
            self.target_transform, self.target_shape = _get_selected_mesh()
        if not self.target_shape:
            self.on_selection_changed()
            return
        try:
            self._ensure_overlay()
            self.result = _collect_geometry(self.target_shape)
            _replace_geom(self.result['geom'])
            counts = self.result['errors']
            label = ('{}\nTriangles: {}   N-gons: {}   Non-manifold edges: {}   '
                     'vertices: {}   Lamina faces: {}').format(
                         self.target_transform.rsplit('|', 1)[-1],
                         len(self.result['triangles']),
                         len(self.result['ngons']),
                         len(counts['nonmanifold_edges']),
                         len(counts['nonmanifold_vertices']),
                         len(counts['lamina_faces']),
                     )
            cmds.text(self.stats, edit=True, label=label)
        except Exception as exc:
            cmds.warning('Topology Inspector: {}'.format(exc))
            traceback.print_exc()
            self.result = None
            _replace_geom()
            cmds.text(self.stats, edit=True, label='Analysis failed. Check Script Editor.')

    def _select_components(self, faces, edges=None, vertices=None):
        if not self.target_transform or not cmds.objExists(self.target_transform):
            return
        edges = edges or []
        vertices = vertices or []
        self._suppress_selection = True
        try:
            items = ([self.target_transform + '.f[{}]'.format(i) for i in sorted(set(faces))] +
                     [self.target_transform + '.e[{}]'.format(i) for i in sorted(set(edges))] +
                     [self.target_transform + '.vtx[{}]'.format(i) for i in sorted(set(vertices))])
            if items:
                cmds.selectMode(component=True)
                cmds.hilite(self.target_transform, replace=True)
                cmds.selectType(allComponents=False)
                cmds.selectType(polymeshFace=True,
                                polymeshEdge=bool(edges),
                                polymeshVertex=bool(vertices))
                cmds.select(items, replace=True)
            else:
                # No matching components: keep the object target active.
                cmds.selectMode(object=True)
                cmds.select(self.target_transform, replace=True)
        finally:
            self._suppress_selection = False

    def select_category(self, category):
        if not self.result:
            self.refresh()
        if not self.result:
            cmds.warning('Please select a polygon mesh first.')
            return
        try:
            if category == 'ngon':
                ids = self.result['ngons']
                _VIEW['errors_visible'] = False
                self._select_components(ids)
                message = 'Selected {} N-gon face(s).'.format(len(ids))
            elif category == 'tri':
                ids = self.result['triangles']
                _VIEW['errors_visible'] = False
                self._select_components(ids)
                message = 'Selected {} triangular face(s).'.format(len(ids))
            elif category == 'both':
                ids = self.result['ngons'] + self.result['triangles']
                _VIEW['errors_visible'] = False
                self._select_components(ids)
                message = 'Selected {} triangular/N-gon face(s).'.format(len(ids))
            elif category == 'bad':
                e = self.result['errors']
                _VIEW['errors_visible'] = True
                faces = e['related_faces']
                edges = e['related_edges']
                vertices = e['nonmanifold_vertices']
                # Highlight both original problematic components and the
                # neighboring faces/edges needed to diagnose the issue.
                self._select_components(faces, edges, vertices)
                message = ('Problem area: {} face(s), {} edge(s), {} vertex/vertices. '
                           '(includes adjacent geometry)').format(
                               len(faces), len(edges), len(vertices))
            else:
                return
            cmds.text(self.status, edit=True, label=message)
            cmds.inViewMessage(amg=message, pos='topCenter', fade=True)
            cmds.refresh(force=True)
        except Exception as exc:
            cmds.warning('Topology Inspector selection failed: {}'.format(exc))
            traceback.print_exc()

    def close(self, *_):
        global _UI
        if self._closing:
            return
        self._closing = True
        for callback_id in self._scene_callbacks:
            try:
                om.MMessage.removeCallback(callback_id)
            except Exception:
                pass
        self._scene_callbacks = []
        _VIEW['errors_visible'] = False
        _replace_geom()
        self._remove_overlay()
        if _UI is self:
            _UI = None


class TIInspectorCommand(om.MPxCommand):
    @staticmethod
    def creator():
        return TIInspectorCommand()

    def doIt(self, args):
        global _UI
        if _UI is not None:
            _UI.close()
        _UI = TopologyInspectorWindow()



# ------------------------ DRAG-AND-DROP INSTALLER ---------------------------
# Maya executes onMayaDroppedPythonFile() when a .py is dropped in the viewport.
# No external packages are required. Files are copied to user preferences only.

_TI_SHELF = 'Custom'
_TI_BUTTON = 'TI_TopologyInspector_Button'
_TI_DOC_TAG = 'TopologyInspector_DragDrop'
_TI_INSTALL_FOLDER = 'TopologyInspector'
_TI_ICON_FILENAME = 'TI_TopologyInspector.png'
_TI_ICON_BASE64 = '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'


def _ti_is_current_version_loaded():
    """Look specifically for v1.4. Prior Inspector versions can stay loaded."""
    for name in (cmds.pluginInfo(query=True, listPlugins=True) or []):
        try:
            cmds_list = cmds.pluginInfo(name, query=True, command=True) or []
            if PLUGIN_COMMAND in cmds_list:
                return True
        except Exception:
            continue
    return False


def _ti_close_older_inspector():
    """Close obsolete UI/overlay if possible; NEVER unload the old plug-in.

    Maya can forbid unloading plug-ins with nodes still in use. A different
    node type and command name let this release coexist with those nodes.
    """
    # When Maya has kept the old plugin Python module in sys.modules, call
    # its own cleanup first so API callbacks are removed as well as the UI.
    import os
    import sys
    for plug in (cmds.pluginInfo(query=True, listPlugins=True) or []):
        try:
            old_commands = cmds.pluginInfo(plug, query=True, command=True) or []
            if not any(c in old_commands for c in ('topologyInspectorUI', 'topologyInspectorV13UI')):
                continue
            module_name = os.path.splitext(os.path.basename(plug))[0]
            module = sys.modules.get(module_name)
            old_ui = getattr(module, '_UI', None)
            if old_ui is not None and hasattr(old_ui, 'close'):
                old_ui.close()
        except Exception as exc:
            cmds.warning('[Topology Inspector] Old window cleanup: {}'.format(exc))

    # A previous drop may have already loaded v1.3. Only close its tool-owned
    # UI/temporary overlay; never unload the plugin or delete user meshes.
    for old_window in ('TopologyInspectorWindow', 'TopologyInspectorWindowV13'):
        try:
            if cmds.window(old_window, exists=True):
                cmds.deleteUI(old_window, window=True)
        except Exception as exc:
            cmds.warning('[Topology Inspector] Could not close prior window: {}'.format(exc))

    for old_type in ('tiViewportOverlayShape', 'tiViewportOverlayShapeV13'):
        try:
            old_shapes = cmds.ls(type=old_type, long=True) or []
        except Exception:
            old_shapes = []
        for shape in old_shapes:
            try:
                parent = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
                if parent and parent.rsplit('|', 1)[-1].startswith('__TI_ViewportOverlay_TEMP'):
                    cmds.delete(parent)
            except Exception as exc:
                cmds.warning('[Topology Inspector] Old overlay could not be removed: {}'.format(exc))


def _ti_shelf_command(installed_plugin_path):
    """Always launch THIS version, never a command from an older plug-in."""
    return (
        "import maya.cmds as cmds\n"
        "_ti_plugin_path = {!r}\n"
        "_ti_loaded = False\n"
        "for _ti_name in (cmds.pluginInfo(query=True, listPlugins=True) or []):\n"
        "    if 'topologyInspectorV14UI' in (cmds.pluginInfo(_ti_name, query=True, command=True) or []):\n"
        "        _ti_loaded = True\n"
        "        break\n"
        "if not _ti_loaded:\n"
        "    cmds.loadPlugin(_ti_plugin_path, quiet=True)\n"
        "cmds.topologyInspectorV14UI()\n"
    ).format(installed_plugin_path.replace('\\', '/'))


def _ti_get_shelf_root():
    """Find Maya's *main* shelf tabs with Python UI commands only.

    Do not evaluate a bare MEL `$gShelfTopLevel;` expression: it is a MEL
    syntax error in Maya. 'ShelfLayout' is the normal name, with a fallback
    search for custom layouts.
    """
    candidates = ['ShelfLayout']
    try:
        candidates.extend(cmds.lsUI(type='shelfTabLayout', long=True) or [])
    except Exception:
        pass

    for name in candidates:
        if name.rsplit('|', 1)[-1] != 'ShelfLayout':
            continue
        try:
            if cmds.shelfTabLayout(name, exists=True):
                return name
        except Exception:
            pass
    # Some customized workspaces rename the shelf tab group.
    for name in candidates:
        try:
            if cmds.shelfTabLayout(name, exists=True):
                return name
        except Exception:
            pass
    raise RuntimeError('Maya shelf UI not found. Please enable the Shelf in Maya UI preferences.')


def _ti_install_shelf(plugin_path, icon_path):
    import os

    shelf_root = _ti_get_shelf_root()
    shelves = cmds.shelfTabLayout(shelf_root, query=True, childArray=True) or []
    shelf_path = next((sh for sh in shelves if sh.rsplit('|', 1)[-1] == _TI_SHELF), None)
    if not shelf_path:
        shelf_path = cmds.shelfLayout(_TI_SHELF, parent=shelf_root)
        cmds.shelfTabLayout(shelf_root, edit=True, tabLabel=(shelf_path, _TI_SHELF))

    # A fixed button name + docTag prevents duplicate buttons from drops.
    existing = None
    for child in (cmds.shelfLayout(shelf_path, query=True, childArray=True) or []):
        if not cmds.shelfButton(child, exists=True):
            continue
        if child.rsplit('|', 1)[-1] == _TI_BUTTON:
            existing = child
            break
        try:
            if cmds.shelfButton(child, query=True, docTag=True) == _TI_DOC_TAG:
                existing = child
                break
        except Exception:
            pass

    kwargs = dict(
        annotation='Topology Inspector: N-gons, Triangles, Non-manifold',
        image1=icon_path.replace('\\', '/'),
        imageOverlayLabel='TI',
        label='Topology Inspector',
        sourceType='python',
        command=_ti_shelf_command(plugin_path),
        docTag=_TI_DOC_TAG,
    )
    if existing:
        cmds.shelfButton(existing, edit=True, **kwargs)
    else:
        cmds.shelfButton(_TI_BUTTON, parent=shelf_path, **kwargs)

    cmds.shelfTabLayout(shelf_root, edit=True, selectTab=shelf_path)

    # Maya's saveShelf writes shelf_Custom.mel; this must stay a string command.
    shelf_file_base = os.path.join(cmds.internalVar(userShelfDir=True), 'shelf_Custom')
    if not cmds.saveShelf(shelf_path, shelf_file_base):
        cmds.warning('[Topology Inspector] Shelf button created but automatic saving failed. '
                     'Save the shelf in the Shelf Editor to persist it.')


def ti_install_from_file(source_path):
    """One-drop install, safe even while the old Inspector plug-in is in use."""
    import os
    import shutil
    import base64

    source_path = os.path.abspath(source_path)
    if not os.path.isfile(source_path):
        raise RuntimeError('Installer source not found: ' + source_path)

    installed_dir = os.path.join(cmds.internalVar(userScriptDir=True), _TI_INSTALL_FOLDER)
    os.makedirs(installed_dir, exist_ok=True)
    # Use a version-specific filename as well as version-specific Maya names.
    installed_path = os.path.join(installed_dir, 'topology_inspector_v14.py')
    loaded = _ti_is_current_version_loaded()
    same_file = os.path.normcase(source_path) == os.path.normcase(os.path.abspath(installed_path))

    if not loaded:
        if not same_file:
            shutil.copy2(source_path, installed_path)
        cmds.loadPlugin(installed_path, quiet=True)
    elif not os.path.isfile(installed_path) and not same_file:
        shutil.copy2(source_path, installed_path)

    # Releasing previous v1.1 and v1.2 is NOT necessary. Old plugin might
    # be in use, so never force unload or try to unregister its node types.
    _ti_close_older_inspector()

    icon_dir = cmds.internalVar(userBitmapsDir=True)
    os.makedirs(icon_dir, exist_ok=True)
    icon_path = os.path.join(icon_dir, _TI_ICON_FILENAME)
    with open(icon_path, 'wb') as icon_file:
        icon_file.write(base64.b64decode(_TI_ICON_BASE64))

    _ti_install_shelf(installed_path, icon_path)
    getattr(cmds, PLUGIN_COMMAND)()
    message = 'Topology Inspector v1.4 installed. Click TI in the Custom shelf.'
    print('[Topology Inspector] ' + message)
    try:
        cmds.inViewMessage(amg=message, pos='topCenter', fade=True)
    except Exception:
        pass
    return installed_path


def onMayaDroppedPythonFile(*args):
    """Maya special callback for dropping the script onto a viewport."""
    import os
    try:
        return ti_install_from_file(os.path.abspath(__file__))
    except Exception as exc:
        traceback.print_exc()
        cmds.warning('[Topology Inspector] Installation failed: {}'.format(exc))
        raise


def initializePlugin(obj):
    plugin = om.MFnPlugin(obj, 'Topology Inspector', '1.4.0', 'Any')
    try:
        plugin.registerNode(PLUGIN_NODE, NODE_ID, TIOverlayNode.creator,
                            TIOverlayNode.initialize, om.MPxNode.kLocatorNode,
                            CLASSIFICATION)
        omr.MDrawRegistry.registerDrawOverrideCreator(
            CLASSIFICATION, REGISTRANT_ID, TIOverlayDraw.creator)
        plugin.registerCommand(PLUGIN_COMMAND, TIInspectorCommand.creator)
    except Exception:
        # Clean up partial registration in reverse order.
        try:
            plugin.deregisterCommand(PLUGIN_COMMAND)
        except Exception:
            pass
        try:
            omr.MDrawRegistry.deregisterDrawOverrideCreator(CLASSIFICATION, REGISTRANT_ID)
        except Exception:
            pass
        try:
            plugin.deregisterNode(NODE_ID)
        except Exception:
            pass
        raise


def uninitializePlugin(obj):
    global _UI
    if _UI is not None:
        _UI.close()
        _UI = None
    if cmds.window(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)
    plugin = om.MFnPlugin(obj)
    plugin.deregisterCommand(PLUGIN_COMMAND)
    omr.MDrawRegistry.deregisterDrawOverrideCreator(CLASSIFICATION, REGISTRANT_ID)
    plugin.deregisterNode(NODE_ID)
