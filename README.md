# Topology Inspector for Maya

A Python-based **topology inspection tool for Autodesk Maya** that highlights triangles, N-gons, and non-manifold problem areas directly in Viewport 2.0. Select matching components with one click and install the tool by dragging a single Python file into Maya.

**Status:** Initial public version (V1). The installer has passed Python syntax checks and scripted mock tests, but has **not yet been fully validated in a running copy of Maya**. Please report any errors along with your Maya version and the Script Editor traceback.

## Features

| Feature | Description |
| --- | --- |
| 🔴 N-gon visualization | Red translucent fill and thick red boundaries for polygons with **5 or more sides**. |
| 🔵 Triangle visualization | Blue translucent fill and thick blue boundaries for **3-sided polygons**. |
| Select all N-gons | Select every N-gon face on the active polygon mesh. |
| Select all triangles | Select every triangle face on the active polygon mesh. |
| Select both | Select triangle and N-gon faces together. |
| 🟡 Find non-manifold + lamina | Detect non-manifold edges and vertices and lamina faces; highlight associated faces, edges, and vertices in yellow and select related components. |
| Adjustable viewport display | Toggle overlays and adjust edge width and face opacity. |
| Refresh | Reanalyze the selected mesh after topology edits. |

**Note:** The yellow diagnostic mode looks for **non-manifold** topology (errors), not ordinary valid manifold geometry. The selection includes neighboring components to help locate problem regions.

## Requirements

- Autodesk Maya with **Python 3** and **Viewport 2.0** (intended for Maya 2022+; individual releases are not yet verified).
- A visible Maya Shelf area for automatic Shelf button installation.
- No third-party Python packages required.

## Installation: drag and drop

1. Download [`TopologyInspector_DragDrop_V1.py`](./TopologyInspector_DragDrop_V1.py). On GitHub, open the file, click **Raw**, and save it with the `.py` extension, or download the repository ZIP and extract the file.
2. Launch **Autodesk Maya**.
3. Drag the `.py` file from File Explorer/Finder **into a Maya viewport** (the 3D view, not Script Editor). If Maya asks permission to execute the script, review and approve it.
4. The installer should create or reuse the **Custom** Shelf, add a **TI** icon, and open the **Topology Inspector** window.
5. Next time you launch Maya, open the tool from the **TI** button on the **Custom** Shelf.

The drag-and-drop file contains its own Shelf icon image. **No separate PNG is required to install it.** The installer copies the Python script into your Maya user scripts directory and places the icon in your user bitmaps directory; the Shelf button points to this installed copy.

### Using the inspector

1. Select a polygon mesh in the viewport or Outliner. The tool scans the active mesh and shows counts for triangles, N-gons, non-manifold elements, and lamina faces.
2. Look for **red** (N-gons) and **blue** (triangles) overlays in Viewport 2.0.
3. Use **SELECT ALL N-GONS**, **SELECT ALL TRIANGLES**, or **SELECT BOTH** to select matching polygon faces.
4. Press **FIND NON-MANIFOLD + LAMINA** to mark problem topology in **yellow** and select its associated components.
5. After editing topology, click **REFRESH SELECTED MESH / CLEAR YELLOW** to rescan.

The inspector uses a temporary viewport overlay rather than changing your existing shader or vertex colors. It creates a temporary Maya node for the overlay, which it removes before saving the scene and restores afterward.

## Troubleshooting

**Drop does not open the tool**
- Make sure you drop the `.py` file into the **3D viewport**, not the Outliner, Shelf, or Script Editor.
- Open **Windows > General Editors > Script Editor** and look for Python errors. Check whether execution of dropped scripts is blocked by Maya security settings.

**The TI button does not appear**
- Make sure the **Shelf** interface is visible and inspect the **Custom** tab.
- If automatic Shelf saving fails, use the Shelf Editor to save the Shelf manually.

**The viewport remains unchanged**
- Select a **polygon mesh** and make sure its topology contains triangles or N-gons.
- Ensure Viewport 2.0 is active and **Show viewport color overlay** is enabled, then press **Refresh**.
- An all-quad mesh will normally have **no red or blue problem highlights**.

**An earlier version is still loaded**
- This V1 installer uses separate internal plugin identifiers so it does not need to forcibly unload older versions. It attempts to close old inspector windows and clean up their temporary overlays.
- If Maya continues to show unexpected behavior, restart Maya and drop the latest file again. Include the Script Editor traceback when reporting an issue.

## Technical notes

- Built with `maya.cmds`, Maya Python API 2.0 (`maya.api.OpenMaya`), and the Viewport 2.0 draw API (`maya.api.OpenMayaRender`).
- The public release filename ends in **`_V1.py`**. Internal plugin identifiers intentionally retain their version-specific suffixes for compatibility with older loaded plugin versions; **do not rename those internal identifiers without reviewing migration behavior**.
- The tool analyzes one active polygon mesh at a time, not every mesh in the scene.
- The script does not repair topology; it highlights and selects components for inspection.

## License

No license has been selected yet. Publishing the source on GitHub does not automatically grant redistribution or modification rights. The repository owner can add a license later.
