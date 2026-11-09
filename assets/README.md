# Launcher Identity

The source is `launcher-logo.svg`: a white E with a coral gallery-page edge on
deep green. Broad strokes and a solid silhouette keep it readable at 16 pixels
on both light and dark taskbars. The PNGs retain transparency around the tile.

`launcher.ico` contains independent 16, 20, 24, 32, 40, 48, 64, 128 and 256 pixel
frames for Windows shortcuts and the native fallback. The PNGs supply Tk with
independent 16, 32, 48, 64 and 256 pixel images so title bars and taskbars do not
resize the largest frame unnecessarily. `launcher-32.png` is also the heading
mark. The launcher has its own AppUserModelID instead of Python's grouping.

Rebuild after editing the geometric SVG (Pillow needed only for this step):

```powershell
python scripts/build_launcher_icon.py
python scripts/build_launcher_icon.py --check
```

The renderer intentionally supports only the source's rectangles and polygons;
it is not a general SVG renderer. The running launcher uses Tk and the supplied
assets, without requiring Pillow or any additional package.
