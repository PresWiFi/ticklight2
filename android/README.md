# Ticklight for Android

The full Ticklight app in its own WebView, so Chrome's screen time limit doesn't apply to it. On top of the web app:

- **Notifications** with working buttons (Done, Took them, Snooze…) via UnifiedPush
- **Widget**: top 3 of Focus, tick them off from the home screen, 🎤 and + to capture
- **Quick add**: long-press the icon (Voice add, Quick add, Add wish, Focus session), the share sheet
  ("Add to Ticklight"), or the quick settings tile
- **Wishlist**: share a product from any shop app and pick "Add to wishlist"; trim the name, tap Add.
  The link (and a £/$/€ price, if in the text) is kept
- Mic permission for voice capture in the app

## Install

On the phone, download
<https://github.com/PresWiFi/ticklight2/releases/download/android-latest/ticklight.apk>
and open it (allow installs from your browser when asked). Every push to `main` that touches
`android/` rebuilds it.

Open the app, enter your server address, sign in.

## Passkeys on the login page

The app's WebView can use passkeys only for sites that list the app. Serve
[`assetlinks.json`](assetlinks.json) at `https://<your Authentik host>/.well-known/assetlinks.json`
(the host that shows the login page, not Ticklight's). For example, with nginx in front of Authentik:

```nginx
location = /.well-known/assetlinks.json {
    default_type application/json;
    alias /etc/nginx/ticklight-assetlinks.json;
}
```

Traefik / Nginx Proxy Manager / Caddy: add the same static file route for that one path.
Check it with `curl https://<your Authentik host>/.well-known/assetlinks.json`. Android caches the
check; if login still fails right after, clear Google Play services' cache or wait a few hours.

The fingerprint in the file belongs to the release key in the repo secrets. Without that file,
"Error creating credential" shows up on passkey steps; log in with password (+ TOTP) instead.

## Notifications

Android apps outside Google Play's Firebase need a push app to deliver messages. Ticklight uses
[UnifiedPush](https://unifiedpush.org) with **ntfy**:

1. Install **ntfy** from the Play Store and open it once (no account needed).
2. In Ticklight: ⚙ → Notifications → **Enable notifications**.

Messages are end-to-end encrypted (Web Push); ntfy only relays them. To avoid ntfy.sh, self-host
ntfy and point the ntfy app at it.

## Signing

CI signs with the key in the `ANDROID_KEYSTORE_B64` / `ANDROID_KEYSTORE_PASSWORD` secrets. Without
them it uses a throwaway debug key, and each build needs an uninstall before installing. Make a key
once:

```
keytool -genkeypair -keystore ticklight.jks -storetype PKCS12 -alias ticklight -keyalg RSA \
  -keysize 3072 -validity 10000 -dname "CN=Ticklight"     # use the same password for both prompts
base64 -w0 ticklight.jks | gh secret set ANDROID_KEYSTORE_B64 -R PresWiFi/ticklight2
gh secret set ANDROID_KEYSTORE_PASSWORD -R PresWiFi/ticklight2     # paste the password
```

Keep `ticklight.jks` somewhere safe; losing it means uninstalling to update.

## Build locally

Open `android/` in Android Studio, or `gradle assembleRelease` with the Android SDK installed.
