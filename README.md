# Desktop Pet for Arch Linux + Hyprland — v8

This is a conservative rollback of the 3D animation loader to the supplied working code. It keeps later model-centering and Spring Bone scaling changes, continues to use CDN imports, and supports sizes up to 2000 px.

## Update your existing source checkout

Extract this archive over the existing source project, then run from that project's root:

```bash
./install.sh
GDK_BACKEND=wayland ~/.local/bin/desktop-pet
```

The installer stops the installed process before updating its files. It copies the source into `~/.local/share/desktop-pet` and does not delete your separately stored model pack.

## Load Roxy and test an action

```bash
~/.local/bin/petctl load /mnt/data/Downloads/desktop-pet-hyprland-v2/Roxy
~/.local/bin/petctl resume
~/.local/bin/petctl action twerk
~/.local/bin/petctl status
```

`petctl status` should report `Runtime: 8-stable-animation-core`. If it shows `6-hotfix` or `legacy build`, an older process/controller is still active. Run `~/.local/bin/petctl quit` and start the app again.

## Size

```bash
~/.local/bin/petctl size 800
~/.local/bin/petctl size 2000
```

The maximum is 2000 pixels. A 2000 px avatar on a 1440 px tall monitor will extend beyond the visible screen.

## Module loading

The viewer deliberately uses the known-working CDN import map. It does not try to mix a partial local `vendor/` install with remote modules. Internet access is required when WebKit loads the 3D viewer.
