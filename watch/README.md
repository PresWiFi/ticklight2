# Ticklight for Galaxy Watch

Open it, speak, done. What you say goes to Ticklight's capture (AI sorts it like any brain dump).
Works without the phone (Wear OS 3+: Galaxy Watch4 and newer).

## Build

Either:
- **GitHub Actions**: push this repo, open **Actions → Build watch app → Run workflow**, download the
  `ticklight-watch` artifact (an APK).
- **Android Studio**: open the `watch/` folder, then Build → Build APK.

## Install

1. Watch: Settings → About watch → Software → tap *Software version* 5 times (developer mode).
2. Settings → Developer options → turn on **ADB debugging** and **Wireless debugging**. Note the IP and port.
3. From a computer on the same Wi-Fi (with Android platform-tools):
   ```
   adb pair <ip>:<pairing port>      # code shown on the watch
   adb connect <ip>:<port>
   adb install ticklight-watch.apk
   ```

The APK is signed with a debug key that changes each CI build; uninstall the old one first if
`adb install` refuses an update.

## Set up

1. Open Ticklight on the watch and enter your server address.
2. It shows a 6-digit code. In the Ticklight web app: ⚙ → Devices → **Pair your watch**, type the code.
3. From then on, opening the app starts listening straight away.

**Fastest launch:** Settings → Advanced features → Customize keys → Double press → Ticklight.
Double-press the home button, speak, done.

If the watch has no speech recognizer, the app shows a text box; the keyboard's mic key works too.
