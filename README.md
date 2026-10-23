# TV toolkit: debloat a TCL Android TV / Google TV and install a minimal home screen

Everything is **reversible**: packages are only *disabled* (`pm disable-user --user 0`), never
uninstalled. A backup is taken before the first change, and `restore.sh` undoes all of it.

## 1. On the TV (one time)
1. Settings > Device Preferences > About > tap **Build** 7 times.
2. Settings > Device Preferences > Developer options > **USB debugging** on
   (newer Google TV: **Wireless debugging**, note the pairing code and IP:port).
3. Same Wi-Fi as your phone. Keep the TV awake.

## 2. Build the home screen (once, no Android SDK needed)
Push this folder to a GitHub repo, open **Actions > Build tvdock.apk > Run workflow**, then
download the `tvdock-apk` artifact and unzip `tvdock.apk` next to `tvtool.py`.
(It is plain Java with no dependencies; the CI build is the first real compile, so if it
fails, send me the log.)

## 3. On your phone (Termux, from F-Droid)
```
pkg install python android-tools
cd tvtoolkit
python tvtool.py            # scan, connect, back up, debloat (asks you), install launcher, test Home
```
Useful variants: `python tvtool.py --ip 192.168.1.50`, `python tvtool.py scan`,
`python tvtool.py debloat`, `python tvtool.py launcher --apk tvdock.apk`, `python tvtool.py verify`.
No `adb` binary? `pip install adb-shell` and add `--pure`.

The first connection shows **Allow USB debugging?** on the TV: choose *Always allow*.

## What it does
1. **Backup** to `tv-data/<ip>/backup-*/`: package lists, settings, props, home app, RAM.
2. **Debloat** in groups, asking first:
   - ads/telemetry/sponsored rows: one yes/no/pick
   - features you might use (Miracast, voice search, screensavers): asked one by one, default *no*
   - streaming apps found on the TV: asked one by one, default *no*
   - suspicious names (heuristic): default *no*; unidentified `com.tcl.*` are left alone
   - Never offered: Inputs menu (`com.tcl.suspension`), HDMI/tuner, remote, Play Services, settings, keyboards.
   - Applied in batches of 10 with a screenshot and health check. An unhealthy batch is re-enabled automatically.
3. **Launcher**: installs TV Dock, lets you pick the dock apps, sets it as Home, disables
   `com.google.android.tungsten.setupwraith` (Google TV Home hijack), then **tests the Home
   button from inside Settings and your first dock app** (checks which activity is in front, plus screenshots).
   If Home still lands on the stock launcher it offers to disable the stock one (reversible).
4. **Report**: `tv-data/<ip>/report.html` shows every screenshot. Open it from Termux with
   `termux-open tv-data/<ip>/report.html` (needs the Termux:API app), or copy the folder to
   Downloads after `termux-setup-storage`.

Screenshot note: video apps and HDMI inputs often screenshot black on TVs (DRM). The tool flags
blank or repeated shots; the Home test does not rely on pixels alone.

## Restore
```
sh tv-data/<ip>/restore.sh                    # re-enable everything, put old Home back
sh tv-data/<ip>/restore.sh --remove-launcher  # ...and remove TV Dock
```
or `python tvtool.py restore`. If the screen is ever black or Home is stuck:
`adb shell pm enable com.google.android.apps.tv.launcherx` (or the stock launcher your TV uses).
Factory reset also clears everything.

## The home screen
- Apple TV-style dock, clock, and a nature wallpaper that crossfades every 10 minutes.
  Scenes are **drawn in code** (alpine dawn, forest mist, desert dusk, ocean sunset, northern lights,
  glacier lake), so nothing downloads and it stays light. Nothing runs while another app is open.
- **All apps** tile: highlight an app and hold OK to add/remove it from the dock.
- Your own photos: `adb push *.jpg /sdcard/Android/data/com.tvdock.home/files/wallpapers/`
  (if the folder is missing, open TV Dock once first). With photos present it rotates those instead.

## Not covered
Turning off ad personalization is a TV setting: Settings > Privacy > Ads > Reset or delete advertising ID.
