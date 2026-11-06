import json
import plistlib
import struct
import zlib
from pathlib import Path

import pytest

from rankwell import code_audit
from rankwell.errors import RankwellError


def write(root: Path, rel: str, content: str | bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content)
    return p


def png(w: int, h: int, color_type: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + chunk + struct.pack(">I", zlib.crc32(chunk))


def plist(data: dict) -> bytes:
    return plistlib.dumps(data)


def flutter_repo(root: Path) -> Path:
    write(root, "pubspec.yaml", "name: demo\n\ndependencies:\n  flutter:\n    sdk: flutter\n  image_picker: ^1.0.0\n"
          "  in_app_review: ^2.0.0\n  google_mobile_ads: ^5.0.0\n  in_app_purchase: ^3.0.0\n\nflutter:\n"
          "  uses-material-design: true\n")
    write(root, "ios/Runner/Info.plist", plist({
        "CFBundleDisplayName": "Totally Different",
        "CFBundleName": "demo",
        "NSPhotoLibraryUsageDescription": "This app needs access to your photos.",
        "NSContactsUsageDescription": "Lets you invite friends from your contacts to a shared list.",
    }))
    write(root, "lib/main.dart", "import 'package:flutter/material.dart';\n\nvoid main() {\n  runApp(App());\n}\n\n"
          "class _State {\n  void initState() {\n    InAppReview.instance.requestReview();\n  }\n}\n")
    write(root, "lib/camera.dart", "final x = picker.pickImage(source: ImageSource.camera);\n"
          "Text('Lorem ipsum dolor');\n// TODO: not a string\nif (name == 'TBD') return;\n"
          "log('TODO later');\nfoo('x'); // TODO: \"quoted\"\n")
    write(root, "lib/store.dart", "const ids = {'com.demo.pro.monthly', 'com.demo.pro.yearly'};\n"
          "final r = await InAppPurchase.instance.queryProductDetails(ids);\nfinal img = 'assets/pro.png';\n")
    write(root, "lib/l10n/app_en.arb", '{"hello": "Hello"}')
    write(root, "lib/l10n/app_de.arb", '{"hello": "TODO translate"}')
    icon = "ios/Runner/Assets.xcassets/AppIcon.appiconset"
    write(root, f"{icon}/Contents.json", json.dumps({"images": [
        {"idiom": "ios-marketing", "size": "1024x1024", "scale": "1x", "filename": "icon-1024.png"},
        {"idiom": "iphone", "size": "60x60", "scale": "3x"},
    ]}))
    write(root, f"{icon}/icon-1024.png", png(1024, 1024, 6))
    write(root, "build/ios/Info.plist", plist({"NSCameraUsageDescription": "x"}))
    write(root, "android/app/src/main/AndroidManifest.xml", "<manifest/>")
    write(root, ".env", "SECRET=1")
    return root


def native_repo(root: Path) -> Path:
    write(root, "Demo.xcodeproj/project.pbxproj",
          '/* */\nINFOPLIST_KEY_CFBundleDisplayName = "Habit Kit";\n'
          'INFOPLIST_KEY_NSCameraUsageDescription = "Habit Kit uses the camera to scan your notes into habits.";\n'
          "PRODUCT_NAME = \"$(TARGET_NAME)\";\nPrivacyInfo.xcprivacy\nITSAppUsesNonExemptEncryption = NO;\n")
    write(root, "Demo/Info.plist", plist({"ITSAppUsesNonExemptEncryption": False}))
    write(root, "Demo/PrivacyInfo.xcprivacy", plist({"NSPrivacyAccessedAPITypes": [
        {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults",
         "NSPrivacyAccessedAPITypeReasons": ["CA92.1"]}]}))
    write(root, "Demo/Demo.entitlements", plist({"com.apple.developer.associated-domains": ["applinks:demo.app"]}))
    write(root, "Demo/ScanView.swift", "import AVFoundation\nlet s = AVCaptureSession()\n")
    write(root, "Demo/DemoApp.swift", "import SwiftUI\nstruct DemoApp: App {\n  var body: some Scene {\n"
          "    WindowGroup { Root().onOpenURL { url in route(url) } }\n  }\n}\n"
          "let d = UserDefaults.standard\n")
    write(root, "Demo/Review.swift", "import StoreKit\nfunc done() {\n  completedCount += 1\n"
          "  if completedCount >= 3 {\n    requestReview()\n  }\n}\n")
    write(root, "Demo/de.lproj/InfoPlist.strings", '"CFBundleDisplayName" = "Habit Kit";\n')
    write(root, "Demo/de.lproj/Localizable.strings", '"hello" = "Hallo";\n')
    icon = "Demo/Assets.xcassets/AppIcon.appiconset"
    write(root, f"{icon}/Contents.json", json.dumps({"images": [
        {"idiom": "universal", "platform": "ios", "size": "1024x1024", "filename": "icon.png"}]}))
    write(root, f"{icon}/icon.png", png(1024, 1024, 2))
    write(root, "Pods/Some/Info.plist", plist({"NSMicrophoneUsageDescription": "x"}))
    write(root, "DemoTests/Fake.swift", "let r = CLLocationManager(); r.requestWhenInUseAuthorization()\n")
    return root


def checks(result: dict) -> dict[str, dict]:
    return {f["check"]: f for f in result["findings"]}


def test_flutter_repo_findings(tmp_path: Path) -> None:
    r = code_audit.audit(flutter_repo(tmp_path), listing_name="Habit Tracker - Demo Habits")
    assert r["kind"] == "flutter"
    c = checks(r)
    missing = c["usage-string-missing"]
    assert missing["severity"] == "high" and "camera" in missing["title"] and missing["file"] == "lib/camera.dart"
    assert c["usage-string-vague"]["evidence"] == "This app needs access to your photos."
    assert "NSContactsUsageDescription" in c["usage-string-unused"]["title"]
    assert c["rating-prompt-early"]["where"] == "lib/main.dart:9"
    assert c["display-name-mismatch"]["severity"] == "medium"
    assert c["flutter-localizations-undeclared"]["evidence"] == "de"
    assert c["icon-alpha"]["severity"] == "high"
    assert c["icon-slots-empty"]["severity"] == "low"
    assert c["att-missing"]["file"] == "pubspec.yaml" and "AdMob" in c["att-missing"]["title"]
    assert set(r["facts"]["product_ids"]) == {"com.demo.pro.monthly", "com.demo.pro.yearly"}
    placeholders = [f for f in r["findings"] if f["check"] == "placeholder-text"]
    assert {f["file"] for f in placeholders} == {"lib/camera.dart", "lib/l10n/app_de.arb"}
    assert "export-compliance" in c and "deep-links-missing" in c
    # build output, other platforms and secrets are never read
    assert not any(f.startswith(("build/", "android/")) for f in r["facts"]["info_plists"])
    assert all(f["severity"] in ("high", "medium", "low") for f in r["findings"])
    sev = [f["severity"] for f in r["findings"]]
    assert sev == sorted(sev, key=["high", "medium", "low"].index)


def test_native_repo_clean(tmp_path: Path) -> None:
    r = code_audit.audit(native_repo(tmp_path), listing_name="Habit Kit: Daily Tracker")
    assert r["kind"] == "native"
    c = checks(r)
    assert r["facts"]["display_name"] == "Habit Kit"
    assert r["facts"]["localizations"] == ["de"]
    assert r["facts"]["associated_domains"] == ["applinks:demo.app"]
    assert r["facts"]["rating_prompt"] == ["Demo/Review.swift:5"]
    for absent in ("usage-string-missing", "usage-string-vague", "rating-prompt-missing", "rating-prompt-early",
                   "display-name-mismatch", "privacy-manifest-missing", "privacy-manifest-reason", "icon-alpha",
                   "icon-1024-missing", "deep-links-unhandled", "export-compliance", "app-not-localized",
                   "display-name-not-localized"):
        assert absent not in c, absent
    assert r["counts"]["high"] == 0


def test_native_repo_problems(tmp_path: Path) -> None:
    root = native_repo(tmp_path)
    (root / "Demo/PrivacyInfo.xcprivacy").unlink()
    (root / "Demo/Assets.xcassets/AppIcon.appiconset/icon.png").write_bytes(png(512, 512, 2))
    write(root, "Demo/AppDelegate.swift", "func application(_ a: UIApplication, didFinishLaunchingWithOptions o: Any)"
          " {\n  ATTrackingManager.requestTrackingAuthorization { _ in }\n}\n")
    write(root, "Demo/Paywall.swift", "struct V: View { var body: some View { Text(\"Enjoying the app?\") } }\n"
          "func ask() {\n  do { try save() } catch { showError() }\n  SKStoreReviewController.requestReview()\n}\n")
    r = code_audit.audit(root, listing_name="Something Else")
    c = checks(r)
    assert c["privacy-manifest-missing"]["severity"] == "high"
    assert c["usage-string-missing"]["title"] == "No permission text for tracking (ATT)"
    assert c["att-at-launch"]["file"] == "Demo/AppDelegate.swift"
    assert c["icon-1024-size"]["severity"] == "high"
    assert c["rating-prompt-after-error"]["file"] == "Demo/Paywall.swift"
    assert "rating-prompt-gated" in c
    assert c["display-name-mismatch"]["evidence"] == "Habit Kit"


def test_react_native_detection_and_missing_prompt(tmp_path: Path) -> None:
    write(tmp_path, "package.json", json.dumps({"dependencies": {"react-native": "0.76.0",
                                                                 "react-native-vision-camera": "4.0.0"}}))
    write(tmp_path, "ios/Demo/Info.plist", plist({"CFBundleDisplayName": "Demo",
                                                  "NSCameraUsageDescription": "Needs camera access"}))
    write(tmp_path, "src/App.tsx", "export default function App() { return null }\n")
    r = code_audit.audit(tmp_path, listing_name="Demo")
    c = checks(r)
    assert r["kind"] == "react-native"
    assert "rating-prompt-missing" in c and c["usage-string-vague"]["severity"] == "medium"
    assert c["privacy-manifest-missing"]["severity"] == "high"
    assert "icon-missing" in c


def test_vague_strings() -> None:
    assert code_audit._is_vague("This app needs access to your camera.")
    assert code_audit._is_vague("Camera access is required for app functionality")
    assert code_audit._is_vague("")
    assert not code_audit._is_vague("Used to scan receipts and attach them to expenses.")
    assert not code_audit._is_vague("PassFirst uses the camera so you can photograph your documents.")


def test_name_match() -> None:
    assert code_audit._name_matches("HabitKit", "Habit Tracker - HabitKit")
    assert code_audit._name_matches("Habit Kit", "HabitKit: Habit Tracker")
    assert not code_audit._name_matches("Runner", "Habit Tracker - HabitKit")


def test_not_a_folder(tmp_path: Path) -> None:
    with pytest.raises(RankwellError):
        code_audit.audit(tmp_path / "nope")


def test_spanish_todo_is_not_a_placeholder(tmp_path: Path) -> None:
    (tmp_path / "pubspec.yaml").write_text("name: x\n")
    l10n = tmp_path / "lib" / "l10n"
    l10n.mkdir(parents=True)
    (l10n / "app_es.arb").write_text('{"viewAll": "Ver todo", "hint": "TODO write this"}\n')
    found = [f for f in code_audit.audit(tmp_path)["findings"] if f["check"].startswith("placeholder")]
    assert len(found) == 1 and "TODO write this" in (found[0].get("evidence") or "")
