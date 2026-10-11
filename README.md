# Topology Inspector for Maya

**Topology Inspector V2** is a Python-based, non-destructive topology inspection tool for **Autodesk Maya** and **Viewport 2.0**. It can scan multiple polygon objects at once and keep color-coded problem areas visible while you select and inspect other objects.

![Topology Inspector V2 shelf icon](TI_ShelfIcon_V2.png)

**Latest:** [Download the V2 drag-and-drop installer](./TopologyInspector_DragDrop_V2.py) · **Previous:** [V1 installer](./TopologyInspector_DragDrop_V1.py)

> **Testing status:** V2 has passed Python syntax and offline installation/UI mock checks. Full in-Maya viewport and cross-version compatibility testing has not yet been completed. If you encounter an issue, please include your Maya version and the Script Editor traceback.

## What's new in V2

- **Batch inspection:** Select multiple polygon objects or transform groups, then click **CHECK SELECTED OBJECTS**.
- **Objects with Issues list:** Only objects containing N-gons, triangles, or detected non-manifold / lamina topology are listed, with per-object counts (`N / M / T`).
- **Persistent viewport highlights:** Scanned objects keep their highlights when you change Maya's selection or click another result. They persist until hidden, rescanned, or the tool is closed.
- **Per-object visibility:** Check or uncheck individual result rows, or use **CHECK ALL** / **UNCHECK ALL**.
- **Color-coded Issue Types filters:** Red **N-GONS**, yellow **NON-MANIFOLD**, and blue **TRIANGLES** buttons toggle their own overlays; disabled buttons appear gray.
- **Color-coded component selection:** Red **SELECT N-GONS**, blue **SELECT TRIANGLES**, purple **SELECT BOTH**, and yellow **SELECT NON-MANIFOLD** act on the last clicked result object.
- **Recheck Listed:** Refresh stored geometry and counts after editing or moving the meshes.
- **Current-shelf installer:** Drag-and-drop installation adds or updates the **TI v2** icon on your **currently selected shelf tab** (rather than automatically creating Custom).
- **Non-destructive display:** Original mesh geometry, materials, UVs, vertex colors, and construction history are left unchanged.

### Color legend

| Issue | Viewport display | Detected topology |
| --- | --- | --- |
| N-gons | 🔴 Red translucent faces and thick outlines | Polygon with 5+ sides |
| Triangles | 🔵 Blue translucent faces and thick outlines | Three-sided polygon |
| Non-manifold / lamina | 🟡 Yellow related faces, bad edges and vertices | Non-manifold edges/vertices and lamina faces identified by Maya |

Yellow is drawn last when problem areas overlap. **Not every triangle is an error:** triangles and N-gons are inspection categories, while non-manifold and lamina geometry are typically defects for a manifold mesh workflow.

## Requirements

- Autodesk Maya **2022 or later** (intended compatibility; not tested across all releases)
- **Python 3** and **Viewport 2.0**
- A visible Maya shelf for automatic installation
- No external Python packages needed

## Install V2 (drag and drop)

1. [Download `TopologyInspector_DragDrop_V2.py`](./TopologyInspector_DragDrop_V2.py). Use **Raw** on GitHub and save it with `.py`, or download the repository ZIP and extract it.
2. Open Maya and select the **Shelf tab where you want the icon to appear**.
3. Drag the `.py` file from your file manager into a **3D Viewport** (not into Script Editor). Review/allow Maya's security prompt if you trust the script.
4. The installer copies its Python runtime into your Maya user scripts folder, writes the embedded **TI v2** icon to your Maya user bitmaps folder, adds or updates the button on the selected shelf, and opens the tool.
5. In future sessions, click the **TI v2** shelf icon to open the inspector.

**One-file installer:** The PNG shown in this repository is for preview and documentation. The shelf icon is already **embedded inside the Python installer**, so the PNG does **not** need to be downloaded separately.

**Upgrading from previous V2 builds:** Download the new `TopologyInspector_DragDrop_V2.py` and drag it into Maya again with the target Shelf selected. This maintenance build uses a new **internal Maya command and runtime filename** so Maya does not accidentally relaunch a previously loaded V2 / V2.1 build. It updates the matching V2 Shelf button. The legacy V1 button is not replaced. The window title should read **Topology Inspector V2 | Updated 2026-10-11**.

If the old window remains, close it. If the Shelf still shows the old icon after installation, restart Maya once to refresh Maya's image cache, then click the updated V2 button. If the tool appears with the old title, the old Shelf button may still exist in another Shelf tab; install V2 on that tab or remove its old button via Shelf Editor.

### Alternative launch (after installation)

Click the **TI v2** shelf button. Advanced users can also open Maya **Script Editor > Python** and run:

```python
import maya.cmds as cmds
cmds.topologyInspectorV2B20261011UI()
```

The long build-specific suffix above is an **internal cache/upgrade identifier**, not the public release number. Do not change internal Maya command / node IDs without updating the plugin migration logic.

## Usage

1. Select all polygon objects or groups you want to inspect in Maya.
2. Click **CHECK SELECTED OBJECTS**. The result list is replaced with objects containing one or more of the three categories; clean objects are not listed.
3. Click an object's name in **OBJECTS WITH ISSUES** to focus it. Highlights on the other checked objects remain visible.
4. Toggle an object's checkbox or use **CHECK ALL / UNCHECK ALL** to control per-object overlays.
5. Click red, yellow, or blue **ISSUE TYPES** buttons to switch category visibility for all checked objects independently. An `[OFF]` filter turns gray without removing scan results.
6. To inspect selected components, click a result row and use the matching colored **SELECT COMPONENTS** buttons.
7. After editing or moving any inspected object, click **RECHECK LISTED** to refresh stored geometry and issue counts.

## Limitations / troubleshooting

- This tool is a **diagnostic overlay and selector**, not a mesh repair or automatic retopology tool.
- Display positions and counts are a **snapshot** of the inspected objects. Press **RECHECK LISTED** after scene changes.
- The issue list persists only while the inspector window is open. Closing the window clears the temporary overlay.
- The overlay node is removed before scene saves and restored afterward; original geometry is not modified.
- Standard open-border edges are not automatically considered non-manifold errors.
- Instancing, heavily corrupted meshes, and individual viewport backends require additional testing.
- If drag-and-drop does not work, ensure the file has `.py` extension and is dropped on a Maya viewport with scripting enabled. Check **Windows > General Editors > Script Editor** for Python errors.
- If the icon is missing, make sure the Shelf is visible; the installer adds it to the **active** shelf tab. Save the shelf in Shelf Editor if automatic saving is unsuccessful.
- If previously loaded plugins cause unexpected behavior, restart Maya and launch the latest shelf button.

## Available versions

| Version | File | Scope |
| --- | --- | --- |
| **V2** (latest) | [`TopologyInspector_DragDrop_V2.py`](./TopologyInspector_DragDrop_V2.py) | Multi-object scan, retained list, color filters and selection tools, current shelf |
| V1 (legacy) | [`TopologyInspector_DragDrop_V1.py`](./TopologyInspector_DragDrop_V1.py) | Single-object inspector, Custom Shelf installer |

## Development notes

The tool uses `maya.cmds`, `maya.api.OpenMaya`, `maya.api.OpenMayaUI`, and `maya.api.OpenMayaRender`. Public release filenames use **V1** and **V2**; internal plugin identifiers may differ to support upgrades alongside plugins Maya already has loaded. The 32px and 64px shelf icon images are embedded in the Python installer.

## License

Please consult the repository's `LICENSE` file if one is present. Publication of code without a license does not by itself grant permission to modify or redistribute it.
