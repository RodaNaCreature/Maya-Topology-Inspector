# -*- coding: utf-8 -*-
"""Topology Inspector V2 - multi-mesh topology QA for Autodesk Maya.

Install: drag this Python file into a Maya viewport. It adds one button to the
CURRENTLY SELECTED shelf tab, not to a forced Custom shelf.

Viewport 2.0 overlay: red=N-gons, blue=triangles, yellow=non-manifold/lamina.
The tool only creates a temporary non-renderable overlay node. It never edits
inspected mesh faces, materials, UVs, vertex colors, or construction history.

Compatibility target: Maya 2022+ (Python 3). Maya runtime testing required.
2026-10-11 maintenance update: isolated runtime prevents cached V2.1 UI from reopening.
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


PLUGIN_NODE = 'tiViewportOverlayShapeV2B20261011'
PLUGIN_COMMAND = 'topologyInspectorV2B20261011UI'
# Per-plugin node IDs must be unique in Maya's running session.
NODE_ID = om.MTypeId(0x0013B9E1)
CLASSIFICATION = 'drawdb/geometry/tiViewportOverlayShapeV2B20261011'
REGISTRANT_ID = 'TopologyInspectorVP2RegistrantV2B20261011'
WINDOW_NAME = 'TopologyInspectorWindowV2B20261011'

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
                                  title='Topology Inspector V2 | Updated 2026-10-11',
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
        self.overlay_transform = cmds.createNode('transform', name='__TI_ViewportOverlay_TEMP_V2B20261011__')
        self.overlay_shape = cmds.createNode(PLUGIN_NODE, parent=self.overlay_transform,
                                              name='__TI_ViewportOverlayShape_TEMP_V2B20261011__')
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
_TI_ICON_FILE = 'TI_TopologyInspector_V2_20261011B_32.png'
_TI_ICON_FILE_LARGE = 'TI_TopologyInspector_V2_20261011B_64.png'
_TI_INSTALLED_FOLDER = 'TopologyInspector'
_TI_ICON_BASE64 = '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'
_TI_ICON_LARGE_BASE64 = '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'


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
                        'topologyInspectorV14UI', 'topologyInspectorV2UI',
                        'topologyInspectorV21UI', 'topologyInspectorV2B20261011UI')):
                continue
            module = sys.modules.get(os.path.splitext(os.path.basename(plugin))[0])
            ui = getattr(module, '_UI', None)
            if ui and hasattr(ui, 'close'):
                ui.close()
        except Exception as exc:
            cmds.warning('[Topology Inspector] Prior window cleanup: {}'.format(exc))

    for name in ('TopologyInspectorWindow', 'TopologyInspectorWindowV13',
                 'TopologyInspectorWindowV14', 'TopologyInspectorWindowV2',
                 'TopologyInspectorWindowV21'):
        try:
            if cmds.window(name, exists=True):
                cmds.deleteUI(name, window=True)
        except Exception:
            pass
    for node_type in ('tiViewportOverlayShape', 'tiViewportOverlayShapeV13',
                      'tiViewportOverlayShapeV14', 'tiViewportOverlayShapeV2',
                      'tiViewportOverlayShapeV21'):
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
        '    if "topologyInspectorV2B20261011UI" in (cmds.pluginInfo(_ti_plug, query=True, command=True) or []):\n'
        '        _ti_v2_loaded = True\n'
        '        break\n'
        'if not _ti_v2_loaded:\n'
        '    cmds.loadPlugin({!r}, quiet=True)\n'
        'cmds.topologyInspectorV2B20261011UI()\n'
    ).format(installed_file.replace('\\', '/'))


def _install_shelf(installed_file, icon_path):
    shelf = _active_shelf()  # IMPORTANT: never create or switch to Custom
    children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
    existing = None
    for child in children:
        if not cmds.shelfButton(child, exists=True):
            continue
        if child.rsplit('|', 1)[-1] == _TI_BUTTON:
            existing = child
            break
        try:
            if cmds.shelfButton(child, query=True, docTag=True) in (
                    _TI_DOC_TAG, 'TopologyInspector_DragDrop_V21'):
                existing = child
                break
        except Exception:
            pass
    opts = dict(annotation='Topology Inspector V2: color-coded multi-object check',
                label='Topology Inspector V2', image1=icon_path.replace('\\', '/'),
                image2=icon_path.replace('_32.png', '_64.png').replace('\\', '/'),
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
    installed = os.path.join(install_dir, 'TopologyInspector_Runtime_V2_20261011B.py')
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
    icon_large_path = os.path.join(icon_dir, _TI_ICON_FILE_LARGE)
    with open(icon_large_path, 'wb') as f:
        f.write(base64.b64decode(_TI_ICON_LARGE_BASE64))

    shelf_name = _install_shelf(installed, icon_path)
    getattr(cmds, PLUGIN_COMMAND)()
    try:
        if cmds.window(WINDOW_NAME, exists=True):
            cmds.window(WINDOW_NAME, edit=True, title='Topology Inspector V2 | Updated 2026-10-11')
    except Exception:
        pass
    message = 'Topology Inspector V2 (2026-10-11 update) installed on {} shelf'.format(shelf_name)
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
