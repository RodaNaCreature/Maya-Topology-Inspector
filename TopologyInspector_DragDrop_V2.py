# -*- coding: utf-8 -*-
"""Topology Inspector V2 - multi-mesh topology QA for Autodesk Maya.

Install: drag this Python file into a Maya viewport. It adds one button to the
CURRENTLY SELECTED shelf tab, not to a forced Custom shelf.

Viewport 2.0 overlay: red=N-gons, blue=triangles, yellow=non-manifold/lamina.
The tool only creates a temporary non-renderable overlay node. It never edits
inspected mesh faces, materials, UVs, vertex colors, or construction history.

Compatibility target: Maya 2022+ (Python 3). Maya runtime testing required.
"""

import os
import re
import sys
import base64
import shutil
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import maya.api.OpenMayaRender as omr


def maya_useNewAPI():
    """Required for API 2.0 MObject arguments in initializePlugin."""
    pass


PLUGIN_NODE = 'tiViewportOverlayShapeV21'
PLUGIN_COMMAND = 'topologyInspectorV21UI'
# Per-plugin node IDs must be unique in Maya's running session.
NODE_ID = om.MTypeId(0x0013B9D8)
CLASSIFICATION = 'drawdb/geometry/tiViewportOverlayShapeV21'
REGISTRANT_ID = 'TopologyInspectorVP2RegistrantV21'
WINDOW_NAME = 'TopologyInspectorWindowV21'

_KEYS = ('ngon_fill', 'ngon_lines', 'tri_fill', 'tri_lines',
         'error_fill', 'error_lines', 'error_points')
_CATEGORIES = ('ngon', 'nonmanifold', 'triangle')
ISSUE_DRAW_COLORS = {
    'ngon': (1.00, 0.10, 0.12),
    'nonmanifold': (1.00, 0.90, 0.02),
    'triangle': (0.10, 0.40, 1.00),
}
ISSUE_BUTTON_COLORS = {
    'ngon': (0.93, 0.37, 0.38),
    'nonmanifold': (0.98, 0.84, 0.22),
    'triangle': (0.38, 0.62, 0.97),
    'both': (0.72, 0.48, 0.87),
}
BUTTON_OFF_COLOR = (0.30, 0.30, 0.32)
ISSUE_BUTTON_LABELS = {
    'ngon': 'N-GONS',
    'nonmanifold': 'NON-MANIFOLD',
    'triangle': 'TRIANGLES',
}

_VIEW = {'visible': True, 'line_width': 4.0, 'opacity': 0.40}
_GEOM = {}
_UI = None


def _empty_geom():
    return {key: om.MPointArray() for key in _KEYS}


def _set_geom(data):
    global _GEOM
    _GEOM = data
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


_GEOM = _empty_geom()


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
        # Do not hide the color overlay when the user hides regular locators.
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

    def hasUIDrawables(self):
        return True

    def isBounded(self, obj_path, camera_path):
        return False

    def isTransparent(self):
        return True

    def prepareForDraw(self, obj_path, camera_path, frame_context, old_data):
        return None

    def addUIDrawables(self, obj_path, draw, frame_context, data):
        if not _VIEW['visible']:
            return
        draw.beginDrawable(omr.MUIDrawManager.kNonSelectable)
        try:
            draw.setPaintStyle(omr.MUIDrawManager.kFlat)
            try:
                draw.setDepthPriority(omr.MRenderItem.sActiveWireDepthPriority)
            except (AttributeError, RuntimeError):
                pass  # Older Maya builds may not expose this priority to Python.
            opacity = float(_VIEW['opacity'])
            width = float(_VIEW['line_width'])

            def fill(key, r, g, b):
                if len(_GEOM[key]):
                    draw.setColor(om.MColor((r, g, b, opacity)))
                    draw.mesh(omr.MUIDrawManager.kTriangles, _GEOM[key])

            def line(key, r, g, b, line_width):
                if len(_GEOM[key]):
                    draw.setColor(om.MColor((r, g, b, 1.0)))
                    draw.setLineWidth(line_width)
                    draw.mesh(omr.MUIDrawManager.kLines, _GEOM[key])

            # Render yellow last: serious topology faults take priority over
            # triangle / N-gon colors when their geometry overlaps.
            fill('ngon_fill', *ISSUE_DRAW_COLORS['ngon'])
            fill('tri_fill', *ISSUE_DRAW_COLORS['triangle'])
            line('ngon_lines', *ISSUE_DRAW_COLORS['ngon'], width)
            line('tri_lines', *ISSUE_DRAW_COLORS['triangle'], width)
            fill('error_fill', *ISSUE_DRAW_COLORS['nonmanifold'])
            line('error_lines', *ISSUE_DRAW_COLORS['nonmanifold'], max(4.0, width + 1.0))
            if len(_GEOM['error_points']):
                draw.setColor(om.MColor((1.0, 0.97, 0.05, 1.0)))
                draw.setPointSize(max(8.0, width * 2.0))
                draw.mesh(omr.MUIDrawManager.kPoints, _GEOM['error_points'])
        finally:
            draw.endDrawable()


def _extract_indices(lines, component):
    if not lines:
        return set()
    if isinstance(lines, str):
        lines = [lines]
    pattern = re.compile(r'\.' + re.escape(component) + r'\[(\d+)\]')
    return {int(v) for line in lines for v in pattern.findall(line)}


def _mesh_api(shape):
    sel = om.MSelectionList()
    sel.add(shape)
    path = sel.getDagPath(0)
    return path, om.MFnMesh(path)


def _topology_faults(shape, path):
    """Query true non-manifold edges/vertices and lamina faces explicitly."""
    bad_edges = _extract_indices(cmds.polyInfo(shape, nonManifoldEdges=True), 'e')
    bad_vertices = _extract_indices(cmds.polyInfo(shape, nonManifoldVertices=True), 'vtx')
    lamina_faces = _extract_indices(cmds.polyInfo(shape, laminaFaces=True), 'f')

    faces = set(lamina_faces)
    edges = set(bad_edges)
    edge_it = om.MItMeshEdge(path)
    vertex_it = om.MItMeshVertex(path)
    face_it = om.MItMeshPolygon(path)
    for idx in sorted(bad_edges):
        edge_it.setIndex(idx)
        faces.update(int(f) for f in edge_it.getConnectedFaces())
    for idx in sorted(bad_vertices):
        vertex_it.setIndex(idx)
        faces.update(int(f) for f in vertex_it.getConnectedFaces())
        edges.update(int(e) for e in vertex_it.getConnectedEdges())
    for idx in sorted(lamina_faces):
        face_it.setIndex(idx)
        edges.update(int(e) for e in face_it.getEdges())
    return {
        'nonmanifold_edges': bad_edges,
        'nonmanifold_vertices': bad_vertices,
        'lamina_faces': lamina_faces,
        'related_faces': faces,
        'related_edges': edges,
    }


def _analyze_mesh(shape):
    """One inspected mesh, captured as a non-destructive world-space snapshot."""
    path, mesh_fn = _mesh_api(shape)
    points = mesh_fn.getPoints(om.MSpace.kWorld)
    counts, poly_verts = mesh_fn.getVertices()
    triangles, ngons = [], []
    face_verts = {}
    offset = 0
    for face_id, count in enumerate(counts):
        if count == 3:
            triangles.append(face_id)
        elif count > 4:
            ngons.append(face_id)
        if count == 3 or count > 4:
            face_verts[face_id] = tuple(int(x) for x in poly_verts[offset:offset + count])
        offset += count

    errors = _topology_faults(shape, path)
    special_faces = set(triangles) | set(ngons) | errors['related_faces']
    triangulated = {}
    try:
        tri_counts, tri_verts = mesh_fn.getTriangles()
        tri_offset = 0
        for face_id, num in enumerate(tri_counts):
            if face_id in special_faces:
                triangulated[face_id] = tri_verts[tri_offset * 3:(tri_offset + num) * 3]
            tri_offset += num
    except Exception:
        # Invalid geometry can fail triangulation. Its boundary is still drawn
        # and the component is still selectable; avoid an unsafe triangle fan.
        pass

    geom = _empty_geom()
    def face_fill(ids, array):
        for fid in ids:
            for vert in triangulated.get(fid, ()):
                array.append(points[int(vert)])

    face_fill(ngons, geom['ngon_fill'])
    face_fill(triangles, geom['tri_fill'])
    face_fill(sorted(errors['related_faces']), geom['error_fill'])

    def face_outlines(ids, array):
        drawn = set()
        for fid in ids:
            verts = face_verts.get(fid)
            if verts is None:
                verts = [int(i) for i in mesh_fn.getPolygonVertices(fid)]
            for i in range(len(verts)):
                a, b = int(verts[i]), int(verts[(i + 1) % len(verts)])
                key = (min(a, b), max(a, b))
                if key in drawn:
                    continue
                drawn.add(key)
                array.append(points[a])
                array.append(points[b])

    face_outlines(ngons, geom['ngon_lines'])
    face_outlines(triangles, geom['tri_lines'])
    for edge_id in sorted(errors['related_edges']):
        a, b = mesh_fn.getEdgeVertices(edge_id)
        geom['error_lines'].append(points[a])
        geom['error_lines'].append(points[b])
    for vert_id in sorted(errors['nonmanifold_vertices']):
        geom['error_points'].append(points[vert_id])

    return {'shape': shape, 'transform': shape.rsplit('|', 1)[0],
            'triangles': triangles, 'ngons': ngons,
            'errors': errors, 'geom': geom, 'enabled': True}


def _has_issues(record):
    e = record['errors']
    return bool(record['triangles'] or record['ngons'] or
                e['nonmanifold_edges'] or e['nonmanifold_vertices'] or
                e['lamina_faces'])


def _count_bad(record):
    e = record['errors']
    return len(e['nonmanifold_edges']) + len(e['nonmanifold_vertices']) + len(e['lamina_faces'])


def _selected_shapes():
    """Find mesh DAG paths in selected transforms, components and groups."""
    selection = cmds.ls(selection=True, long=True) or []
    found, seen = [], set()
    for item in selection:
        base = item.split('.', 1)[0]
        if not cmds.objExists(base):
            continue
        typ = cmds.nodeType(base)
        candidates = []
        if typ == 'mesh':
            candidates = [base]
        elif typ == 'transform':
            candidates = (cmds.listRelatives(base, shapes=True, noIntermediate=True,
                                             fullPath=True, type='mesh') or [])
            # Selecting a group also includes its mesh children.
            candidates += (cmds.listRelatives(base, allDescendents=True,
                                               fullPath=True, type='mesh') or [])
        for shape in candidates:
            if shape in seen or not cmds.objExists(shape):
                continue
            if shape.rsplit('|', 1)[-1].startswith('__TI_ViewportOverlayShape'):
                continue
            try:
                if cmds.getAttr(shape + '.intermediateObject'):
                    continue
            except Exception:
                pass
            seen.add(shape)
            found.append(shape)
    return found


def _combine_geometry(records, visible_categories):
    """Combine every checked mesh into one single VP2 overlay buffer."""
    combined = _empty_geom()
    mapping = {
        'ngon': ('ngon_fill', 'ngon_lines'),
        'triangle': ('tri_fill', 'tri_lines'),
        'nonmanifold': ('error_fill', 'error_lines', 'error_points'),
    }
    active_keys = tuple(k for c in _CATEGORIES if visible_categories.get(c, False)
                        for k in mapping[c])
    for record in records:
        if not record['enabled'] or not cmds.objExists(record['shape']):
            continue
        for key in active_keys:
            array = combined[key]
            for point in record['geom'][key]:
                array.append(point)
    return combined


class InspectorWindow(object):
    def __init__(self):
        self._closing = False
        self.records = {}  # DAG shape -> analysis; preserved across Maya selection changes
        self.categories = {'ngon': True, 'nonmanifold': True, 'triangle': True}
        self.category_buttons = {}
        self.focus_shape = None
        self.overlay_transform = None
        self.overlay_shape = None
        self.callbacks = []
        self.summary_text = None
        self.status_text = None
        self.rows_layout = None

        if cmds.window(WINDOW_NAME, exists=True):
            cmds.deleteUI(WINDOW_NAME)
        self.window = cmds.window(WINDOW_NAME,
                                  title='Topology Inspector V2 | Multi-Object QA',
                                  sizeable=True, widthHeight=(575, 590))
        main = cmds.columnLayout(adjustableColumn=True, rowSpacing=7,
                                 columnAttach=('both', 12))
        cmds.text(label='TOPOLOGY INSPECTOR  |  MULTI-OBJECT CHECK',
                  font='boldLabelFont', height=29, align='left')
        cmds.text(label='Select meshes (or groups), then CHECK. Results remain visible when selection changes.',
                  align='left')
        cmds.button(label='CHECK SELECTED OBJECTS', height=43,
                    backgroundColor=(0.26, 0.48, 0.38),
                    command=lambda *_: self.check_selected())
        self.summary_text = cmds.text(label='No check performed yet.', align='left',
                                      height=24)
        cmds.separator(style='in', height=7)
        cmds.text(label='ISSUE TYPES  (viewport color filters)',
                  font='boldLabelFont', align='left')
        cmds.rowLayout(numberOfColumns=3, adjustableColumn=3,
                       columnWidth3=(175, 190, 175))
        # Click the colored button to toggle that issue category's viewport overlay.
        for issue in ('ngon', 'nonmanifold', 'triangle'):
            self.category_buttons[issue] = cmds.button(
                label=self.issue_button_label(issue),
                height=38,
                backgroundColor=ISSUE_BUTTON_COLORS[issue],
                enableBackground=True,
                annotation='Toggle {} overlay on all checked objects'.format(ISSUE_BUTTON_LABELS[issue]),
                command=lambda *_, c=issue: self.toggle_category(c))
        cmds.setParent('..')
        cmds.separator(style='in', height=7)
        cmds.text(label='OBJECTS WITH ISSUES  (click an object to select it)',
                  font='boldLabelFont', align='left')
        cmds.rowLayout(numberOfColumns=3, adjustableColumn=3,
                       columnWidth3=(170, 170, 170))
        cmds.button(label='CHECK ALL', height=30,
                    command=lambda *_: self.set_all(True))
        cmds.button(label='UNCHECK ALL', height=30,
                    command=lambda *_: self.set_all(False))
        cmds.button(label='RECHECK LISTED', height=30,
                    command=lambda *_: self.recheck_listed())
        cmds.setParent('..')
        scroll = cmds.scrollLayout(height=230, childResizable=True)
        self.rows_layout = cmds.columnLayout(parent=scroll, adjustableColumn=True,
                                             rowSpacing=2)
        cmds.setParent(main)
        cmds.separator(style='in', height=9)
        cmds.text(label='SELECT COMPONENTS OF LAST CLICKED OBJECT',
                  font='boldLabelFont', align='left')
        cmds.rowLayout(numberOfColumns=4, adjustableColumn=4,
                       columnWidth4=(126, 126, 126, 126))
        cmds.button(label='SELECT N-GONS', height=34,
                    backgroundColor=ISSUE_BUTTON_COLORS['ngon'],
                    enableBackground=True,
                    command=lambda *_: self.select_components('ngon'))
        cmds.button(label='SELECT TRIANGLES', height=34,
                    backgroundColor=ISSUE_BUTTON_COLORS['triangle'],
                    enableBackground=True,
                    command=lambda *_: self.select_components('triangle'))
        cmds.button(label='SELECT BOTH', height=34,
                    backgroundColor=ISSUE_BUTTON_COLORS['both'],
                    enableBackground=True,
                    annotation='Select red N-gons and blue triangles together',
                    command=lambda *_: self.select_components('both'))
        cmds.button(label='SELECT NON-MANIFOLD', height=34,
                    backgroundColor=ISSUE_BUTTON_COLORS['nonmanifold'],
                    enableBackground=True,
                    command=lambda *_: self.select_components('nonmanifold'))
        cmds.setParent('..')
        cmds.checkBox(label='Show viewport overlay', value=True,
                      changeCommand=self.toggle_overlay)
        cmds.floatSliderGrp(label='Outline width', field=True, minValue=1,
                            maxValue=9, fieldMinValue=1, fieldMaxValue=20,
                            value=4, precision=1, changeCommand=self.set_width)
        cmds.floatSliderGrp(label='Face opacity', field=True, minValue=0.08,
                            maxValue=0.85, fieldMinValue=0, fieldMaxValue=1,
                            value=0.4, precision=2, changeCommand=self.set_opacity)
        self.status_text = cmds.text(label='Red: N-gon  |  Yellow: non-manifold / lamina  |  Blue: triangle',
                                     align='left', height=30)
        cmds.showWindow(self.window)
        cmds.scriptJob(uiDeleted=[self.window, self.close], runOnce=True)

        self.ensure_overlay()
        self.callbacks.append(om.MSceneMessage.addCallback(
            om.MSceneMessage.kBeforeSave, self.before_save))
        self.callbacks.append(om.MSceneMessage.addCallback(
            om.MSceneMessage.kAfterSave, self.after_save))
        cmds.scriptJob(event=['SceneOpened', self.reset_scene], parent=self.window,
                       protected=True)
        cmds.scriptJob(event=['NewSceneOpened', self.reset_scene], parent=self.window,
                       protected=True)
        cmds.scriptJob(event=['Undo', self.restore_overlay], parent=self.window,
                       protected=True)
        cmds.scriptJob(event=['Redo', self.restore_overlay], parent=self.window,
                       protected=True)
        self.update_rows()

    def ensure_overlay(self):
        if self.overlay_transform and cmds.objExists(self.overlay_transform):
            return
        self.overlay_transform = cmds.createNode('transform', name='__TI_ViewportOverlay_TEMP_V21__')
        self.overlay_shape = cmds.createNode(PLUGIN_NODE, parent=self.overlay_transform,
                                              name='__TI_ViewportOverlayShape_TEMP_V21__')
        cmds.setAttr(self.overlay_transform + '.hiddenInOutliner', True)
        try:
            cmds.setAttr(self.overlay_shape + '.hiddenInOutliner', True)
        except Exception:
            pass
        cmds.setAttr(self.overlay_transform + '.inheritsTransform', False)
        cmds.setAttr(self.overlay_transform + '.visibility', bool(_VIEW['visible']))

    def remove_overlay(self):
        if self.overlay_transform and cmds.objExists(self.overlay_transform):
            cmds.delete(self.overlay_transform)
        self.overlay_transform = None
        self.overlay_shape = None

    def before_save(self, *_):
        self.remove_overlay()  # prevent plugin node from being saved with .ma/.mb

    def after_save(self, *_):
        if not self._closing:
            self.ensure_overlay()
            self.rebuild_viewport()

    def restore_overlay(self, *_):
        if not self._closing:
            self.ensure_overlay()
            self.rebuild_viewport()

    def reset_scene(self, *_):
        if self._closing:
            return
        self.overlay_transform = None
        self.overlay_shape = None
        self.records.clear()
        self.focus_shape = None
        self.ensure_overlay()
        self.update_rows()
        self.rebuild_viewport()
        self.status('Scene changed. Select meshes and click CHECK.')

    def status(self, message):
        if self.status_text:
            cmds.text(self.status_text, edit=True, label=message)

    def check_selected(self):
        shapes = _selected_shapes()
        if not shapes:
            self.status('No polygon mesh selected. Select objects or groups, then CHECK.')
            cmds.warning('[Topology Inspector] Select at least one polygon mesh.')
            return
        self.run_scan(shapes)

    def run_scan(self, shapes):
        # One scan replaces the old results; selection changes alone do not.
        new_records = {}
        failure = []
        previous_enabled = {shape: rec['enabled'] for shape, rec in self.records.items()}
        scanned = 0
        for shape in shapes:
            if not cmds.objExists(shape):
                continue
            try:
                result = _analyze_mesh(shape)
                scanned += 1
                if _has_issues(result):
                    result['enabled'] = previous_enabled.get(shape, True)
                    new_records[shape] = result
            except Exception as exc:
                failure.append((shape, str(exc)))
                cmds.warning('[Topology Inspector] {}: {}'.format(shape, exc))
                traceback.print_exc()
        self.records = new_records
        if self.focus_shape not in self.records:
            self.focus_shape = next(iter(self.records), None)
        self.ensure_overlay()
        self.update_rows()
        self.rebuild_viewport()
        msg = 'Scanned: {}  |  With issues: {}  |  Clean: {}  |  Failed: {}'.format(
            scanned, len(self.records), scanned - len(self.records), len(failure))
        cmds.text(self.summary_text, edit=True, label=msg)
        self.status('Click an object in the list. Checking visibility remains independent of scene selection.')

    def recheck_listed(self):
        if not self.records:
            self.status('No objects in the list. Select meshes and click CHECK.')
            return
        self.run_scan(list(self.records))

    def update_rows(self):
        for child in cmds.columnLayout(self.rows_layout, query=True, childArray=True) or []:
            cmds.deleteUI(child)
        if not self.records:
            cmds.text(parent=self.rows_layout, label='No objects with flagged topology.',
                      align='left', height=34)
            return
        for shape, record in self.records.items():
            if not cmds.objExists(shape):
                label = '[Deleted] ' + shape.rsplit('|', 1)[-1]
            else:
                label = record['transform'].rsplit('|', 1)[-1]
            if len(label) > 35:
                label = label[:32] + '...'
            row = cmds.rowLayout(parent=self.rows_layout, numberOfColumns=5,
                                 adjustableColumn=2,
                                 columnWidth5=(32, 280, 70, 70, 70), height=30)
            cmds.checkBox(parent=row, label='', value=record['enabled'],
                          annotation='Show or hide highlighted topology for this object',
                          changeCommand=lambda enabled, s=shape: self.toggle_object(s, enabled))
            cmds.button(parent=row, label=label, align='left',
                        annotation=record['transform'],
                        backgroundColor=(0.28, 0.36, 0.45) if shape == self.focus_shape else (0.26, 0.26, 0.26),
                        command=lambda *_, s=shape: self.focus_object(s))
            for issue, short, count in (
                    ('ngon', 'N', len(record['ngons'])),
                    ('nonmanifold', 'M', _count_bad(record)),
                    ('triangle', 'T', len(record['triangles']))):
                cmds.text(parent=row, label='{}: {}'.format(short, count), align='center',
                          backgroundColor=ISSUE_BUTTON_COLORS[issue] if count else BUTTON_OFF_COLOR,
                          enableBackground=True)
            cmds.setParent(self.rows_layout)

    def set_all(self, value):
        for record in self.records.values():
            record['enabled'] = bool(value)
        self.update_rows()
        self.rebuild_viewport()
        self.status(('Showing' if value else 'Hiding') + ' all listed objects.')

    def toggle_object(self, shape, value):
        if shape in self.records:
            self.records[shape]['enabled'] = bool(value)
            self.rebuild_viewport()

    def issue_button_label(self, category):
        return '[ON]  ' + ISSUE_BUTTON_LABELS[category] if self.categories[category] else '[OFF]  ' + ISSUE_BUTTON_LABELS[category]

    def toggle_category(self, category):
        self.set_category(category, not self.categories[category])

    def set_category(self, category, value):
        self.categories[category] = bool(value)
        button = getattr(self, 'category_buttons', {}).get(category)
        if button:
            cmds.button(button, edit=True,
                        label=self.issue_button_label(category),
                        backgroundColor=ISSUE_BUTTON_COLORS[category] if value else BUTTON_OFF_COLOR,
                        enableBackground=True)
        self.rebuild_viewport()
        self.status('{} overlay {}.'.format(ISSUE_BUTTON_LABELS[category], 'ON' if value else 'OFF'))

    def rebuild_viewport(self):
        _set_geom(_combine_geometry(self.records.values(), self.categories))

    def focus_object(self, shape):
        if shape not in self.records:
            return
        rec = self.records[shape]
        if not cmds.objExists(rec['transform']):
            self.status('Object no longer exists. Click CHECK to scan again.')
            return
        self.focus_shape = shape
        try:
            cmds.selectMode(object=True)
            cmds.select(rec['transform'], replace=True)
        except Exception as exc:
            cmds.warning('[Topology Inspector] Cannot select object: {}'.format(exc))
        self.update_rows()
        self.status('Selected: {}   (all checked overlays remain visible)'.format(rec['transform']))

    def select_components(self, category):
        if not self.records:
            self.status('No checked objects yet. Select objects and click CHECK.')
            return
        shape = self.focus_shape if self.focus_shape in self.records else next(iter(self.records))
        rec = self.records[shape]
        if not cmds.objExists(rec['transform']):
            self.status('This object has been deleted. Check again.')
            return
        if category == 'ngon':
            faces, edges, verts = rec['ngons'], (), ()
        elif category == 'triangle':
            faces, edges, verts = rec['triangles'], (), ()
        elif category == 'both':
            faces, edges, verts = rec['ngons'] + rec['triangles'], (), ()
        else:
            errs = rec['errors']
            faces = errs['related_faces']
            edges = errs['related_edges']
            verts = errs['nonmanifold_vertices']
        components = ([rec['transform'] + '.f[{}]'.format(i) for i in sorted(faces)] +
                      [rec['transform'] + '.e[{}]'.format(i) for i in sorted(edges)] +
                      [rec['transform'] + '.vtx[{}]'.format(i) for i in sorted(verts)])
        if not components:
            self.status('No {} components on the focused object.'.format(category))
            return
        try:
            cmds.selectMode(component=True)
            cmds.hilite(rec['transform'], replace=True)
            cmds.selectType(allComponents=False)
            cmds.selectType(polymeshFace=bool(faces), polymeshEdge=bool(edges),
                            polymeshVertex=bool(verts))
            cmds.select(components, replace=True)
            self.status('Selected {} {} component(s) on {}.'.format(
                len(components), category, rec['transform'].rsplit('|', 1)[-1]))
        except Exception as exc:
            self.status('Selection failed. See Script Editor: ' + str(exc))
            traceback.print_exc()

    def toggle_overlay(self, value):
        _VIEW['visible'] = bool(value)
        if self.overlay_transform and cmds.objExists(self.overlay_transform):
            cmds.setAttr(self.overlay_transform + '.visibility', bool(value))
        cmds.refresh(force=True)

    def set_width(self, value):
        _VIEW['line_width'] = float(value)
        cmds.refresh(force=True)

    def set_opacity(self, value):
        _VIEW['opacity'] = float(value)
        cmds.refresh(force=True)

    def close(self, *_):
        global _UI
        if self._closing:
            return
        self._closing = True
        for callback in self.callbacks:
            try:
                om.MMessage.removeCallback(callback)
            except Exception:
                pass
        self.callbacks = []
        _set_geom(_empty_geom())
        self.remove_overlay()
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
        _UI = InspectorWindow()


# ----------------------------- DRAG-DROP INSTALLER --------------------------
_TI_BUTTON = 'TI_TopologyInspector_V2_Button'
_TI_DOC_TAG = 'TopologyInspector_DragDrop_V2'
_TI_ICON_FILE = 'TI_TopologyInspector_V2.png'
_TI_INSTALLED_FOLDER = 'TopologyInspector'
_TI_ICON_BASE64 = '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'


def _plugin_loaded():
    for plugin in cmds.pluginInfo(query=True, listPlugins=True) or []:
        try:
            if PLUGIN_COMMAND in (cmds.pluginInfo(plugin, query=True, command=True) or []):
                return True
        except Exception:
            pass
    return False


def _close_old_tool_windows():
    """Clean up old windows and temporary overlay nodes without unloading plugins."""
    for plugin in cmds.pluginInfo(query=True, listPlugins=True) or []:
        try:
            commands = cmds.pluginInfo(plugin, query=True, command=True) or []
            if not any(x in commands for x in
                       ('topologyInspectorUI', 'topologyInspectorV13UI',
                        'topologyInspectorV14UI', 'topologyInspectorV2UI')):
                continue
            module = sys.modules.get(os.path.splitext(os.path.basename(plugin))[0])
            ui = getattr(module, '_UI', None)
            if ui and hasattr(ui, 'close'):
                ui.close()
        except Exception as exc:
            cmds.warning('[Topology Inspector] Prior window cleanup: {}'.format(exc))

    for name in ('TopologyInspectorWindow', 'TopologyInspectorWindowV13',
                 'TopologyInspectorWindowV14', 'TopologyInspectorWindowV2'):
        try:
            if cmds.window(name, exists=True):
                cmds.deleteUI(name, window=True)
        except Exception:
            pass
    for node_type in ('tiViewportOverlayShape', 'tiViewportOverlayShapeV13',
                      'tiViewportOverlayShapeV14', 'tiViewportOverlayShapeV2'):
        try:
            shapes = cmds.ls(type=node_type, long=True) or []
        except Exception:
            shapes = []
        for shape in shapes:
            parent = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
            if parent and parent.rsplit('|', 1)[-1].startswith('__TI_ViewportOverlay_TEMP'):
                try:
                    cmds.delete(parent)
                except Exception:
                    pass


def _shelf_root():
    candidates = ['ShelfLayout']
    try:
        candidates += cmds.lsUI(type='shelfTabLayout', long=True) or []
    except Exception:
        pass
    for root in candidates:
        try:
            if cmds.shelfTabLayout(root, exists=True):
                return root
        except Exception:
            pass
    raise RuntimeError('Maya shelf is hidden. Show the Shelf first and drag the file again.')


def _active_shelf():
    root = _shelf_root()
    selected = cmds.shelfTabLayout(root, query=True, selectTab=True)
    if not selected:
        raise RuntimeError('No active shelf tab. Open the target shelf first.')
    # selected is typically the short name (e.g. "Polygons")
    children = cmds.shelfTabLayout(root, query=True, childArray=True) or []
    for child in children:
        if child.rsplit('|', 1)[-1] == selected.rsplit('|', 1)[-1]:
            return child
    return selected


def _shelf_command(installed_file):
    return (
        'import maya.cmds as cmds\n'
        '_ti_v2_loaded = False\n'
        'for _ti_plug in (cmds.pluginInfo(query=True, listPlugins=True) or []):\n'
        '    if "topologyInspectorV21UI" in (cmds.pluginInfo(_ti_plug, query=True, command=True) or []):\n'
        '        _ti_v2_loaded = True\n'
        '        break\n'
        'if not _ti_v2_loaded:\n'
        '    cmds.loadPlugin({!r}, quiet=True)\n'
        'cmds.topologyInspectorV21UI()\n'
    ).format(installed_file.replace('\\', '/'))


def _install_shelf(installed_file, icon_path):
    shelf = _active_shelf()  # IMPORTANT: never create or switch to Custom
    children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
    existing = None
    for child in children:
        if not cmds.shelfButton(child, exists=True):
            continue
        if child.rsplit('|', 1)[-1] in (
                _TI_BUTTON, 'TI_TopologyInspector_Button',
                'TI_TopologyInspector_V13_Button', 'TI_TopologyInspector_V14_Button'):
            existing = child
            break
        try:
            if cmds.shelfButton(child, query=True, docTag=True) in (
                    _TI_DOC_TAG, 'TopologyInspector_DragDrop_V21', 'TopologyInspector_DragDrop',
                    'TopologyInspector_DragDrop_V13', 'TopologyInspector_DragDrop_V14'):
                existing = child
                break
        except Exception:
            pass
    opts = dict(annotation='Topology Inspector V2: color-coded multi-object check',
                label='Topology Inspector V2', image1=icon_path.replace('\\', '/'),
                imageOverlayLabel='', sourceType='python',
                command=_shelf_command(installed_file), docTag=_TI_DOC_TAG)
    if existing:
        cmds.shelfButton(existing, edit=True, **opts)
    else:
        cmds.shelfButton(_TI_BUTTON, parent=shelf, **opts)

    # Shelf base file name must match the actual active shelf, not "Custom".
    shelf_name = shelf.rsplit('|', 1)[-1]
    if not re.match(r'^[A-Za-z0-9_]+$', shelf_name):
        cmds.warning('[Topology Inspector] Shelf button added. Save this shelf in '
                     'Shelf Editor to persist it (custom shelf name).')
        return shelf_name
    shelf_base = os.path.join(cmds.internalVar(userShelfDir=True), 'shelf_' + shelf_name)
    try:
        if not cmds.saveShelf(shelf, shelf_base):
            cmds.warning('[Topology Inspector] Shelf button added but automatic shelf save failed. '
                         'Save it via Shelf Editor.')
    except Exception as exc:
        cmds.warning('[Topology Inspector] Shelf save failed (button works this session): {}'.format(exc))
    return shelf_name


def ti_install_from_file(source):
    source = os.path.abspath(source)
    if not os.path.isfile(source):
        raise RuntimeError('File not found: ' + source)

    # Check target shelf before performing plug-in actions, to avoid half installs.
    _active_shelf()
    install_dir = os.path.join(cmds.internalVar(userScriptDir=True), _TI_INSTALLED_FOLDER)
    os.makedirs(install_dir, exist_ok=True)
    installed = os.path.join(install_dir, 'TopologyInspector_Runtime_V2.py')
    same_file = os.path.normcase(source) == os.path.normcase(os.path.abspath(installed))
    if not _plugin_loaded():
        if not same_file:
            shutil.copy2(source, installed)
        cmds.loadPlugin(installed, quiet=True)
    elif not os.path.isfile(installed) and not same_file:
        shutil.copy2(source, installed)

    _close_old_tool_windows()  # no unloading; older plugin node types are different
    icon_dir = cmds.internalVar(userBitmapsDir=True)
    os.makedirs(icon_dir, exist_ok=True)
    icon_path = os.path.join(icon_dir, _TI_ICON_FILE)
    with open(icon_path, 'wb') as f:
        f.write(base64.b64decode(_TI_ICON_BASE64))

    shelf_name = _install_shelf(installed, icon_path)
    getattr(cmds, PLUGIN_COMMAND)()
    try:
        if cmds.window(WINDOW_NAME, exists=True):
            cmds.window(WINDOW_NAME, edit=True, title='Topology Inspector V2 | Multi-Object QA')
    except Exception:
        pass
    message = 'Topology Inspector V2 installed on {} shelf'.format(shelf_name)
    print('[Topology Inspector] ' + message)
    try:
        cmds.inViewMessage(amg=message, pos='topCenter', fade=True)
    except Exception:
        pass
    return installed


def onMayaDroppedPythonFile(*args):
    try:
        return ti_install_from_file(os.path.abspath(__file__))
    except Exception as exc:
        traceback.print_exc()
        cmds.warning('[Topology Inspector] Install failed: {}'.format(exc))
        raise


def initializePlugin(obj):
    plugin = om.MFnPlugin(obj, 'Topology Inspector', '2.0.0', 'Any')
    try:
        plugin.registerNode(PLUGIN_NODE, NODE_ID, TIOverlayNode.creator,
                            TIOverlayNode.initialize, om.MPxNode.kLocatorNode,
                            CLASSIFICATION)
        omr.MDrawRegistry.registerDrawOverrideCreator(
            CLASSIFICATION, REGISTRANT_ID, TIOverlayDraw.creator)
        plugin.registerCommand(PLUGIN_COMMAND, TIInspectorCommand.creator)
    except Exception:
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
