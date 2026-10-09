#!/bin/sh
# Flipparr View to TestFlight, in one command (docs/OFFLINE_READING_PLAN.md,
# Phase 7): build the web bundle, copy it into the iOS project, archive a
# Release build, and upload it to App Store Connect.
#
# Run from a clean checkout of the commit to ship, on a Mac with Xcode. The
# credentials come from the environment and never from the repository:
#
#   ASC_KEY_ID      App Store Connect API key id
#   ASC_ISSUER_ID   its issuer id
#   ASC_KEY_PATH    the .p8 file App Store Connect gave you
#   DEVELOPMENT_TEAM  the signing team, unless ios/Signing.local.xcconfig
#                     (git-ignored) already says it
#
# The build number is the UTC time, so every upload is higher than the last,
# as App Store Connect requires; the version shown to testers is the
# project's MARKETING_VERSION. Outputs go to a folder outside the repository
# (FLIPPARR_RELEASE_OUT, default ~/flipparr-view-releases/<time>).
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
app="$here/v1-prototype"

for name in ASC_KEY_ID ASC_ISSUER_ID ASC_KEY_PATH; do
  eval "value=\${$name:-}"
  [ -n "$value" ] || { echo "Set $name (see the top of this script)." >&2; exit 2; }
done
[ -f "$ASC_KEY_PATH" ] || { echo "ASC_KEY_PATH does not point at a file." >&2; exit 2; }

team="${DEVELOPMENT_TEAM:-}"
if [ -z "$team" ] && [ -f "$app/ios/Signing.local.xcconfig" ]; then
  team=$(sed -n 's/^[[:space:]]*DEVELOPMENT_TEAM[[:space:]]*=[[:space:]]*\([A-Z0-9]*\).*/\1/p' "$app/ios/Signing.local.xcconfig" | head -1)
fi
[ -n "$team" ] || { echo "Set DEVELOPMENT_TEAM, or put it in ios/Signing.local.xcconfig." >&2; exit 2; }

# What ships is a commit: the app's About names it (__VIEW_BUILD__).
if [ -n "$(git -C "$here" status --porcelain -- v1-prototype/src v1-prototype/reader-app v1-prototype/ios/App/App)" ]; then
  echo "v1-prototype has uncommitted changes; commit them first so the build names what it is." >&2
  exit 1
fi

stamp=$(date -u +%Y%m%d%H%M)
out="${FLIPPARR_RELEASE_OUT:-$HOME/flipparr-view-releases/$stamp}"
mkdir -p "$out"

echo "Building the app's web bundle and copying it into iOS…"
(cd "$app" && npm run --silent build:reader-app && npx cap copy ios >/dev/null)

echo "Archiving build $stamp…"
xcodebuild -project "$app/ios/App/App.xcodeproj" -scheme App -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "$out/FlipparrView.xcarchive" \
  -allowProvisioningUpdates -authenticationKeyPath "$ASC_KEY_PATH" \
  -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID" \
  DEVELOPMENT_TEAM="$team" CURRENT_PROJECT_VERSION="$stamp" \
  archive >"$out/archive.log" 2>&1 || { echo "Archive failed; see $out/archive.log" >&2; exit 1; }

cat >"$out/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string>$team</string>
  <key>signingStyle</key><string>automatic</string>
  <key>uploadSymbols</key><true/>
</dict>
</plist>
PLIST

echo "Uploading to App Store Connect…"
xcodebuild -exportArchive -archivePath "$out/FlipparrView.xcarchive" \
  -exportOptionsPlist "$out/ExportOptions.plist" -exportPath "$out/export" \
  -allowProvisioningUpdates -authenticationKeyPath "$ASC_KEY_PATH" \
  -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID" >"$out/upload.log" 2>&1 || { echo "Upload failed; see $out/upload.log" >&2; exit 1; }

echo "Uploaded build $stamp. It appears in TestFlight once App Store Connect has processed it."
