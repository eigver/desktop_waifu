# Local JavaScript dependencies

The 3D viewer intentionally uses local ES modules only. Keep the downloaded libraries here; the ZIP does not include third-party distributions.

The v6 Python server resolves multiple common layouts. It looks for these logical entry points:

- Three.js module: `three/three.module.js` or `three/build/three.module.js`
- GLTFLoader: `three/addons/loaders/GLTFLoader.js` or `three/examples/jsm/loaders/GLTFLoader.js`
- `three-vrm`: `three-vrm/lib/three-vrm.module.min.js` (or non-minified / `dist` variants)
- `three-vrm-animation`: `three-vrm-animation/lib/three-vrm-animation.module.min.js` (or non-minified / `dist` variants)

Check the folders with:

```bash
find vendor -type f | sort
bash tools/check_vendor.sh
```

`install.sh` copies the contents of `vendor/` into `~/.local/share/desktop-pet/vendor/`. Re-run `./install.sh` after updating the source project so the running app receives changes to `viewer3d.html` and `desktop_pet.py`.
