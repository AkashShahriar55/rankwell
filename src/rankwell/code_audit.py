"""ASO and App Review checks on an app's source code (Flutter, native Swift/Objective-C, React Native).

Every finding is backed by something found in the repo (a file and line, a plist key, a dependency), says why
it matters for ranking, conversion or App Review, and how to fix it. Build output, Pods, node_modules and
secrets (.env, .p8 and similar) are never read.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .errors import RankwellError

SEVERITIES = ("high", "medium", "low")
MAX_FILES = 40_000
MAX_BYTES = 1_500_000

SKIP_DIRS = {
    ".git", ".hg", ".svn", "build", "Build", "DerivedData", "Pods", "node_modules", ".dart_tool", ".symlinks",
    ".pub-cache", ".gradle", ".idea", ".vscode", ".fvm", "Carthage", ".build", "vendor", ".venv", "venv",
    "__pycache__", ".expo", "coverage", "ephemeral", ".aso", ".swiftpm", "xcuserdata", ".bundle", "fastlane",
}
SKIP_DIRS_CROSS = {"android", "linux", "windows", "web", "macos"}  # other platforms of Flutter / RN projects
SECRET_RE = re.compile(r"(^\.env.*|.*\.(p8|p12|pem|key|keystore|jks|mobileprovision|cer)$|^GoogleService-Info\.plist$"
                       r"|^google-services\.json$|^credentials.*|^secrets?\..*)", re.I)
CODE_EXT = {".swift", ".m", ".mm", ".h", ".dart", ".js", ".jsx", ".ts", ".tsx"}
TEXT_EXT = CODE_EXT | {".plist", ".strings", ".xcstrings", ".arb", ".json", ".yaml", ".yml", ".pbxproj",
                       ".entitlements", ".xcprivacy", ".storekit", ".resolved", ".lock"}
TEST_PARTS = {"test", "tests", "Tests", "UITests", "__tests__", "__mocks__", "integration_test", "spec", "e2e"}
COMMENT_RE = re.compile(r"^\s*(//|#|\*|/\*|<!--)")
LANG_RE = re.compile(r"^[a-z]{2,3}([-_][A-Za-z]{2,4})?$")


@dataclass
class Finding:
    check: str
    severity: str
    title: str
    why: str
    fix: str
    file: str | None = None
    line: int | None = None
    evidence: str | None = None
    rules: list[str] = field(default_factory=list)

    def where(self) -> str:
        if not self.file:
            return "-"
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass(frozen=True)
class Perm:
    label: str
    keys: tuple[str, ...]
    swift: str = ""
    dart: str = ""
    js: str = ""
    packages: tuple[str, ...] = ()


PERMS = (
    Perm("camera", ("NSCameraUsageDescription",),
         r"AVCaptureDevice|AVCaptureSession|sourceType\s*=\s*\.camera|\.SourceType\.camera|"
         r"VNDocumentCameraViewController|DataScannerViewController",
         r"ImageSource\.camera|Permission\.camera\b|CameraController\(|MobileScanner\(",
         r"launchCamera\(|useCameraPermissions?\(|requestCameraPermissionsAsync|PERMISSIONS\.IOS\.CAMERA",
         ("camera", "mobile_scanner", "qr_code_scanner", "qr_code_scanner_plus", "flutter_barcode_scanner",
          "react-native-vision-camera", "react-native-camera", "expo-camera", "expo-barcode-scanner",
          "react-native-qrcode-scanner", "cunning_document_scanner")),
    Perm("photo library", ("NSPhotoLibraryUsageDescription",),
         r"PHPhotoLibrary\.requestAuthorization|PHAsset\.fetchAssets|PHImageManager",
         r"ImageSource\.gallery|Permission\.photos\b|PhotoManager\.",
         r"launchImageLibrary\(|MediaLibrary\.requestPermissionsAsync|CameraRoll\.getPhotos|"
         r"PERMISSIONS\.IOS\.PHOTO_LIBRARY\b",
         ("image_picker", "photo_manager", "wechat_assets_picker", "@react-native-camera-roll/camera-roll",
          "expo-media-library", "react-native-image-crop-picker", "react-native-image-picker")),
    Perm("saving to photos", ("NSPhotoLibraryAddUsageDescription",),
         r"UIImageWriteToSavedPhotosAlbum|UISaveVideoAtPathToSavedPhotosAlbum|PHAssetChangeRequest|"
         r"PHAssetCreationRequest",
         r"GallerySaver\.|ImageGallerySaver\.|Gal\.put|Permission\.photosAddOnly",
         r"CameraRoll\.save|MediaLibrary\.saveToLibraryAsync|MediaLibrary\.createAssetAsync",
         ("gallery_saver", "image_gallery_saver", "gal", "saver_gallery")),
    Perm("microphone", ("NSMicrophoneUsageDescription",),
         r"AVAudioRecorder|requestRecordPermission|inputNode\.installTap|AVCaptureDevice\.default\(for:\s*\.audio",
         r"Permission\.microphone|AudioRecorder\(|FlutterSoundRecorder|SpeechToText\(",
         r"Audio\.requestPermissionsAsync|Audio\.Recording|AudioRecorderPlayer|Voice\.start|"
         r"PERMISSIONS\.IOS\.MICROPHONE|useMicrophonePermission",
         ("record", "flutter_sound", "speech_to_text", "audio_waveforms", "react-native-audio-recorder-player",
          "@react-native-voice/voice", "expo-speech-recognition")),
    Perm("speech recognition", ("NSSpeechRecognitionUsageDescription",),
         r"SFSpeechRecognizer", r"SpeechToText\(|Permission\.speech",
         r"Voice\.start|ExpoSpeechRecognitionModule|PERMISSIONS\.IOS\.SPEECH_RECOGNITION",
         ("speech_to_text", "@react-native-voice/voice", "expo-speech-recognition")),
    Perm("location", ("NSLocationWhenInUseUsageDescription", "NSLocationAlwaysAndWhenInUseUsageDescription"),
         r"requestWhenInUseAuthorization|CLLocationUpdate\.liveUpdates|startUpdatingLocation|requestLocation\(\)",
         r"Geolocator\.|Permission\.location\b|Permission\.locationWhenInUse",
         r"Geolocation\.getCurrentPosition|Geolocation\.watchPosition|requestForegroundPermissionsAsync|"
         r"PERMISSIONS\.IOS\.LOCATION_WHEN_IN_USE",
         ("geolocator", "location", "react-native-geolocation-service", "@react-native-community/geolocation",
          "expo-location")),
    Perm("location in the background", ("NSLocationAlwaysAndWhenInUseUsageDescription",),
         r"requestAlwaysAuthorization", r"Permission\.locationAlways",
         r"requestBackgroundPermissionsAsync|PERMISSIONS\.IOS\.LOCATION_ALWAYS"),
    Perm("contacts", ("NSContactsUsageDescription",),
         r"CNContactStore", r"FlutterContacts\.|ContactsService\.|Permission\.contacts",
         r"Contacts\.requestPermissionsAsync|Contacts\.getAll|PERMISSIONS\.IOS\.CONTACTS",
         ("flutter_contacts", "contacts_service", "fast_contacts", "react-native-contacts", "expo-contacts")),
    Perm("calendar", ("NSCalendarsUsageDescription", "NSCalendarsFullAccessUsageDescription",
                      "NSCalendarsWriteOnlyAccessUsageDescription"),
         r"requestAccess\(to:\s*\.event|requestFullAccessToEvents|requestWriteOnlyAccessToEvents",
         r"DeviceCalendarPlugin|Permission\.calendar",
         r"requestCalendarPermissionsAsync|RNCalendarEvents\.|PERMISSIONS\.IOS\.CALENDARS",
         ("device_calendar", "react-native-calendar-events", "expo-calendar")),
    Perm("reminders", ("NSRemindersUsageDescription", "NSRemindersFullAccessUsageDescription"),
         r"requestAccess\(to:\s*\.reminder|requestFullAccessToReminders", r"Permission\.reminders",
         r"requestRemindersPermissionsAsync|PERMISSIONS\.IOS\.REMINDERS"),
    Perm("Face ID", ("NSFaceIDUsageDescription",),
         r"LAContext|evaluatePolicy", r"LocalAuthentication\(",
         r"LocalAuthentication\.authenticateAsync|ReactNativeBiometrics|TouchID\.authenticate",
         ("local_auth", "react-native-biometrics", "expo-local-authentication", "react-native-touch-id")),
    Perm("Health data", ("NSHealthShareUsageDescription", "NSHealthUpdateUsageDescription"),
         r"HKHealthStore", r"Health\(\)|HealthFactory\(", r"AppleHealthKit\.",
         ("health", "react-native-health")),
    Perm("motion and fitness", ("NSMotionUsageDescription",),
         r"CMMotionActivityManager|CMPedometer", r"Pedometer\.|Permission\.sensors", r"Pedometer\.",
         ("pedometer",)),
    Perm("Bluetooth", ("NSBluetoothAlwaysUsageDescription",),
         r"CBCentralManager|CBPeripheralManager", r"FlutterBluePlus|FlutterReactiveBle|Permission\.bluetooth",
         r"new BleManager\(|BleManager\.start|PERMISSIONS\.IOS\.BLUETOOTH",
         ("flutter_blue_plus", "flutter_blue", "flutter_reactive_ble", "react-native-ble-plx",
          "react-native-ble-manager")),
    Perm("tracking (ATT)", ("NSUserTrackingUsageDescription",),
         r"requestTrackingAuthorization", r"AppTrackingTransparency\.|Permission\.appTrackingTransparency",
         r"requestTrackingPermission|requestTrackingPermissionsAsync|PERMISSIONS\.IOS\.APP_TRACKING_TRANSPARENCY",
         ("app_tracking_transparency", "react-native-tracking-transparency", "expo-tracking-transparency")),
    Perm("Apple Music and media library", ("NSAppleMusicUsageDescription",),
         r"MPMediaLibrary|MPMediaQuery|MusicAuthorization", r"Permission\.mediaLibrary", "",
         ("on_audio_query",)),
    Perm("local network", ("NSLocalNetworkUsageDescription",), r"NWBrowser|NetServiceBrowser"),
    Perm("NFC", ("NFCReaderUsageDescription",), r"NFCNDEFReaderSession|NFCTagReaderSession",
         r"NfcManager\.", r"NfcManager\.", ("nfc_manager", "flutter_nfc_kit", "react-native-nfc-manager")),
)

RATING_CALL = {
    "swift": r"requestReview\s*\(|SKStoreReviewController\.requestReview",
    "dart": r"\.requestReview\s*\(",
    "js": r"requestReview\s*\(|RequestInAppReview\s*\(",
}
RATING_PASSIVE = r"action=write-review|openStoreListing\s*\(|StoreReview\.storeUrl"
RATING_PACKAGES = ("in_app_review", "rate_my_app", "app_review", "react-native-in-app-review", "expo-store-review",
                   "react-native-rate", "react-native-store-review")
LAUNCH_FILE_RE = re.compile(r"(AppDelegate|SceneDelegate|Onboarding|onboarding|Splash|splash|Welcome|welcome|"
                            r"Intro\b|intro_|main\.dart$|_layout\.(tsx|jsx)$|^App\.(tsx|jsx|ts|js)$)")
LAUNCH_CTX_RE = re.compile(r"didFinishLaunching|initState\s*\(|useEffect\(\s*\(\)\s*=>|componentDidMount|"
                           r"viewDidLoad|\.onAppear|\.task\s*\{|firstLaunch|first_launch|isFirstLaunch|onboarding",
                           re.I)
GUARD_RE = re.compile(r"[Cc]ount|launch(es|Count)|sessions?\b|[Dd]ays?Since|last(Prompt|Review|Asked)|"
                      r">=|>\s*\d|%\s*\d|UserDefaults|SharedPreferences|AsyncStorage|@AppStorage|prefs\b|"
                      r"completed|didComplete|success|isEligible|shouldAsk|canAsk|shouldRequest")
ERROR_RE = re.compile(r"\bcatch\b|\.failure\b|onError|\bfailed\b|\bthrow\b|showError|errorMessage")
GATING_RE = re.compile(r"[Ee]njoying|enjoy using|[Dd]o you (like|love|enjoy)|[Aa]re you (happy|enjoying)|"
                       r"[Ll]ove (the|this|our) app|[Hh]ow (would|do) you rate|[Rr]ate us|5 stars|five stars")

ADS_SDKS = {
    "google_mobile_ads": "Google AdMob", "react-native-google-mobile-ads": "Google AdMob",
    "Google-Mobile-Ads-SDK": "Google AdMob", "GoogleMobileAds": "Google AdMob",
    "swift-package-manager-google-mobile-ads": "Google AdMob",
    "facebook_app_events": "Meta SDK", "react-native-fbsdk-next": "Meta SDK", "FBSDKCoreKit": "Meta SDK",
    "facebook-ios-sdk": "Meta SDK", "FacebookCore": "Meta SDK",
    "appsflyer_sdk": "AppsFlyer", "react-native-appsflyer": "AppsFlyer", "AppsFlyerFramework": "AppsFlyer",
    "AppsFlyerLib": "AppsFlyer", "appsflyerframework": "AppsFlyer",
    "adjust_sdk": "Adjust", "react-native-adjust": "Adjust", "Adjust": "Adjust", "adjust_signature_sdk": "Adjust",
    "applovin_max": "AppLovin", "react-native-applovin-max": "AppLovin", "AppLovinSDK": "AppLovin",
    "unity_ads_plugin": "Unity Ads", "UnityAds": "Unity Ads", "ironsource_mediation": "ironSource",
    "IronSourceSDK": "ironSource", "flutter_branch_sdk": "Branch", "react-native-branch": "Branch",
    "Branch": "Branch", "singular_flutter_sdk": "Singular", "Singular-SDK": "Singular",
    "tiktok_business_sdk": "TikTok", "kochava_tracker": "Kochava",
}
ANALYTICS_SDKS = {
    "firebase_analytics": "Firebase Analytics", "@react-native-firebase/analytics": "Firebase Analytics",
    "Firebase/Analytics": "Firebase Analytics", "FirebaseAnalytics": "Firebase Analytics",
    "mixpanel_flutter": "Mixpanel", "mixpanel-react-native": "Mixpanel", "Mixpanel-swift": "Mixpanel",
    "amplitude_flutter": "Amplitude", "@amplitude/analytics-react-native": "Amplitude", "AmplitudeSwift": "Amplitude",
    "posthog_flutter": "PostHog", "posthog-react-native": "PostHog", "PostHog": "PostHog",
}
SWIFT_SDK_IMPORTS = {"GoogleMobileAds": "Google AdMob", "FBSDKCoreKit": "Meta SDK", "AppsFlyerLib": "AppsFlyer",
                     "AdjustSdk": "Adjust", "AppLovinSDK": "AppLovin", "FirebaseAnalytics": "Firebase Analytics"}
IAP_PACKAGES = {"in_app_purchase": "in_app_purchase", "purchases_flutter": "RevenueCat",
                "react-native-purchases": "RevenueCat", "react-native-iap": "react-native-iap",
                "expo-in-app-purchases": "expo-in-app-purchases", "RevenueCat": "RevenueCat",
                "purchases-ios": "RevenueCat", "flutter_inapp_purchase": "flutter_inapp_purchase"}
IAP_CODE_RE = re.compile(r"Product\.products\(for|SKProductsRequest|queryProductDetails|getProducts\(|"
                         r"getSubscriptions\(|fetchProducts\(|productIdentifiers|productIds|kProductIds|"
                         r"ProductDetails|import StoreKit|StoreKit\.|Purchases\.", re.I)
PRODUCT_ID_RE = re.compile(r"[\"']([A-Za-z0-9_]+(?:\.[A-Za-z0-9_\-]+){1,6})[\"']")
PRODUCT_WORD_RE = re.compile(r"(pro|premium|plus|month|year|annual|week|lifetime|subscri|sub\b|iap|unlock|coin|gem|"
                             r"tip|remove_?ads|noads|full|vip)", re.I)
FILE_LIKE_RE = re.compile(r"\.(png|jpe?g|gif|webp|json|dart|swift|js|ts|tsx|ttf|otf|mp3|mp4|wav|svg|plist|html|"
                          r"css|txt|md|arb|lottie|riv)$", re.I)
PLACEHOLDER_RE = re.compile(r"lorem ipsum|\bTODO\b|\bFIXME\b|\bTBD\b|\bXXX+\b|placeholder text|your text here|"
                            r"sample text|\basdf\b", re.I)
UI_CTX_RE = re.compile(r"Text\(|Label\(|Button\(|\b(title|subtitle|label|message|content|description|hint)\s*:|"
                       r"labelText|hintText|placeholder|navigationTitle|alert|SnackBar|Tooltip|<Text|\btr\(|"
                       r"NSLocalizedString|String\(localized", re.I)
LOG_RE = re.compile(r"\b(print|debugPrint|log|logger|NSLog|os_log|assert|console\.\w+)\s*[.(]", re.I)
REQUIRED_REASON_RE = re.compile(r"\bUserDefaults\b|\bNSUserDefaults\b|@AppStorage|systemUptime|mach_absolute_time|"
                                r"\.creationDate|\.modificationDate|volumeAvailableCapacity|NSFileSystemFreeSize|"
                                r"activeInputModes")
LINK_HANDLER = {
    "swift": r"onOpenURL|continue userActivity|NSUserActivityTypeBrowsingWeb|webpageURL|onContinueUserActivity|"
             r"open url: URL|openURLContexts",
    "dart": r"AppLinks\(|uriLinkStream|getInitialLink|getInitialUri|GoRouter\(|onGenerateRoute|"
            r"FlutterDeepLinkingEnabled",
    "js": r"Linking\.(getInitialURL|addEventListener)|useURL\(|linking\s*[=:]\s*\{|expo-router|useLinkTo",
}
VAGUE_STRIP_RE = re.compile(r"\b(access(ing)? (to )?(your |the )?[\w ]{0,20}?(camera|photos?|library|microphone|"
                            r"location|contacts|calendars?|reminders|data|bluetooth|health)|permission to (access|use)"
                            r"( your| the)?( \w+)?|uses? (your |the )?\w+)\b", re.I)
PURPOSE_RE = re.compile(r"\b(to|so|for|when|while|lets|allows?|helps?|enables?)\b\s+\w+", re.I)
GENERIC_PURPOSE_RE = re.compile(r"(app functionality|to work properly|to function|better experience|full experience|"
                                r"all features|some features|certain features|for features|proper functioning|"
                                r"improve (the|your) experience)", re.I)
PROMISE_RE = re.compile(r"\b(coming soon|in the future|future (version|update|feature)s?|upcoming|later version|"
                        r"will soon|soon be able)\b", re.I)


class _Repo:
    """Files of an app repo, read lazily, with the skip rules applied."""

    def __init__(self, root: Path, cross_platform: bool) -> None:
        self.root = root
        self.files: list[str] = []
        self.dirs: list[str] = []
        self._cache: dict[str, str] = {}
        skip = SKIP_DIRS | (SKIP_DIRS_CROSS if cross_platform else set())
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith("."))
            rel_dir = os.path.relpath(dirpath, root)
            for d in dirnames:
                self.dirs.append(d if rel_dir == "." else f"{rel_dir}/{d}")
            for name in sorted(filenames):
                if SECRET_RE.match(name):
                    continue
                self.files.append(name if rel_dir == "." else f"{rel_dir}/{name}")
                if len(self.files) >= MAX_FILES:
                    return

    def text(self, rel: str) -> str:
        if rel not in self._cache:
            p = self.root / rel
            try:
                self._cache[rel] = "" if p.stat().st_size > MAX_BYTES else p.read_text(encoding="utf-8",
                                                                                        errors="replace")
            except OSError:
                self._cache[rel] = ""
        return self._cache[rel]

    def named(self, name: str) -> list[str]:
        return [f for f in self.files if f.rsplit("/", 1)[-1] == name]

    def ext(self, *exts: str) -> list[str]:
        return [f for f in self.files if os.path.splitext(f)[1] in exts]

    def code(self, lang: str) -> list[str]:
        exts = {"swift": (".swift", ".m", ".mm"), "dart": (".dart",), "js": (".js", ".jsx", ".ts", ".tsx")}[lang]
        return [f for f in self.ext(*exts) if not is_test(f)]

    def grep(self, files: list[str], pattern: str | re.Pattern[str]) -> Iterator[tuple[str, int, str]]:
        """(file, line number, line) for each non-comment line matching pattern."""
        rx = re.compile(pattern) if isinstance(pattern, str) else pattern
        for f in files:
            text = self.text(f)
            if not text or not rx.search(text):
                continue
            for n, line in enumerate(text.splitlines(), start=1):
                if rx.search(line) and not COMMENT_RE.match(line):
                    yield f, n, line


def is_test(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in TEST_PARTS or p.endswith(("Tests", "UITests")) for p in parts[:-1]) or bool(
        re.search(r"(_test\.dart|\.test\.[jt]sx?|\.spec\.[jt]sx?|Tests?\.swift)$", parts[-1]))


def _line_of(text: str, needle: str) -> int | None:
    i = text.find(needle)
    return text.count("\n", 0, i) + 1 if i >= 0 else None


def _load_plist(data: bytes) -> dict[str, Any] | None:
    try:
        v = plistlib.loads(data)
    except Exception:
        return None
    return v if isinstance(v, dict) else None


def _load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def detect_kind(root: Path) -> str:
    pubspec = root / "pubspec.yaml"
    if pubspec.is_file() and re.search(r"^\s*flutter\s*:", pubspec.read_text(errors="replace"), re.M):
        return "flutter"
    pkg = root / "package.json"
    if pkg.is_file():
        data = _load_json(pkg.read_text(errors="replace")) or {}
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})} if isinstance(data, dict) else {}
        if "react-native" in deps or "expo" in deps:
            return "react-native"
    if any(root.glob("*.xcodeproj")) or any(root.glob("*/*.xcodeproj")) or (root / "Package.swift").exists():
        return "native"
    if any(root.glob("*/*.swift")) or any(root.glob("*.swift")):
        return "native"
    return "unknown"


class _Audit:
    def __init__(self, root: Path, listing_name: str | None) -> None:
        self.root = root
        self.kind = detect_kind(root)
        self.repo = _Repo(root, cross_platform=self.kind in ("flutter", "react-native"))
        self.listing_name = listing_name
        self.findings: list[Finding] = []
        self.facts: dict[str, Any] = {"kind": self.kind}
        self.deps: dict[str, tuple[str, int | None]] = {}
        self.keys: dict[str, list[tuple[Any, str, int | None]]] = {}
        self.main_plists: list[str] = []
        self.expo: dict[str, Any] = {}

    def add(self, check: str, severity: str, title: str, why: str, fix: str, file: str | None = None,
            line: int | None = None, evidence: str | None = None, rules: list[str] | None = None) -> None:
        self.findings.append(Finding(check, severity, title, why, fix, file, line, evidence, rules or []))

    # Inputs -----------------------------------------------------------------------------------------------------

    def load(self) -> None:
        self._load_deps()
        self._load_plists()
        if self.kind == "react-native":
            self._load_expo()

    def _load_deps(self) -> None:
        r = self.repo
        for f in r.named("pubspec.yaml"):
            section = None
            for n, line in enumerate(r.text(f).splitlines(), start=1):
                top = re.match(r"^([A-Za-z_]+)\s*:", line)
                if top:
                    section = top.group(1)
                    continue
                m = re.match(r"^  ([A-Za-z0-9_]+)\s*:", line)
                if m and section in ("dependencies", "dev_dependencies"):
                    self.deps.setdefault(m.group(1), (f, n))
        for f in r.named("package.json"):
            data = _load_json(r.text(f))
            if isinstance(data, dict):
                for name in data.get("dependencies", {}) or {}:
                    self.deps.setdefault(name, (f, _line_of(r.text(f), f'"{name}"')))
        for f in r.named("Podfile"):
            for n, line in enumerate(r.text(f).splitlines(), start=1):
                m = re.match(r"^\s*pod\s+['\"]([^'\"]+)['\"]", line)
                if m:
                    self.deps.setdefault(m.group(1), (f, n))
                    self.deps.setdefault(m.group(1).split("/")[0], (f, n))
        for f in r.named("Package.resolved") + r.named("Package.swift"):
            text = r.text(f)
            for m in re.finditer(r'"identity"\s*:\s*"([^"]+)"|github\.com/[^/"]+/([A-Za-z0-9_.\-]+?)(?:\.git)?"',
                                 text):
                name = m.group(1) or m.group(2)
                self.deps.setdefault(name, (f, text.count("\n", 0, m.start()) + 1))

    def _load_plists(self) -> None:
        r = self.repo
        for f in r.named("Info.plist"):
            if is_test(f):
                continue
            text = r.text(f)
            data = _load_plist((self.root / f).read_bytes()) if text else None
            if data is None:
                if text:
                    self.add("plist-unreadable", "low", "Info.plist could not be parsed",
                             "Rankwell could not check its permission strings and app name.",
                             "Open it in Xcode and fix the XML.", f)
                continue
            if "NSExtension" in data or "WKAppBundleIdentifier" in data:
                continue
            self.main_plists.append(f)
            for k, v in data.items():
                self.keys.setdefault(k, []).append((v, f, _line_of(text, f"<key>{k}</key>")))
        for f in r.named("project.pbxproj"):
            text = r.text(f)
            for m in re.finditer(r'INFOPLIST_KEY_(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]+);', text):
                v = m.group(2).strip()
                v = v[1:-1].replace('\\"', '"') if v.startswith('"') else v
                self.keys.setdefault(m.group(1), []).append((v, f, text.count("\n", 0, m.start()) + 1))
        self.facts["info_plists"] = self.main_plists

    def _load_expo(self) -> None:
        f = "app.json"
        if f in self.repo.files:
            data = _load_json(self.repo.text(f))
            expo = data.get("expo", data) if isinstance(data, dict) else {}
            self.expo = expo if isinstance(expo, dict) else {}
            ios = self.expo.get("ios") or {}
            text = self.repo.text(f)
            for k, v in (ios.get("infoPlist") or {}).items():
                self.keys.setdefault(k, []).append((v, f, _line_of(text, f'"{k}"')))
        self.facts["expo_managed"] = bool(self.expo) and not any(d.startswith("ios") for d in self.repo.dirs)

    def key(self, name: str) -> tuple[Any, str, int | None] | None:
        found = self.keys.get(name)
        return found[0] if found else None

    def evidence(self, swift: str, dart: str, js: str, packages: tuple[str, ...]) -> list[tuple[str, int | None]]:
        out: list[tuple[str, int | None]] = []
        for lang, rx in (("swift", swift), ("dart", dart), ("js", js)):
            if rx:
                out += [(f, n) for f, n, _ in self.repo.grep(self.repo.code(lang), rx)]
        out += [self.deps[p] for p in packages if p in self.deps]
        return out

    # Checks -----------------------------------------------------------------------------------------------------

    def permissions(self) -> None:
        used_keys: set[str] = set()
        cannot_check = self.facts.get("expo_managed") and not self.keys
        for perm in PERMS:
            ev = self.evidence(perm.swift, perm.dart, perm.js, perm.packages)
            if not ev:
                continue
            used_keys.update(perm.keys)
            if any(self.key(k) for k in perm.keys) or cannot_check:
                continue
            f, n = ev[0]
            self.add("usage-string-missing", "high", f"No permission text for {perm.label}",
                     f"The code uses {perm.label} ({f}) but the app has no {perm.keys[0]}. iOS ends the app the "
                     "moment it asks without this text, and App Review rejects it (Guideline 5.1.1).",
                     f"Add {perm.keys[0]} to Info.plist with a sentence that says what the app does with it and "
                     "gives an example, e.g. \"<App> uses the camera to scan receipts so you can attach them to an "
                     "expense.\"", f, n, rules=["rev-permission-strings"])
        for k, entries in self.keys.items():
            if not (k.endswith("UsageDescription") or k == "NFCReaderUsageDescription"):
                continue
            value, f, n = entries[0]
            text = str(value).strip()
            if "$(" in text and len(text) < 30:
                continue
            if PROMISE_RE.search(text):
                self.add("usage-string-promise", "medium", f"{k} talks about a feature that is not there yet",
                         "Permission text must describe what the app does today. Promising future features is "
                         "misleading and App Review checks the text against the app (Guidelines 2.3 and 5.1.1).",
                         "Describe only the current use, with a concrete example.", f, n, text,
                         ["rev-permission-strings", "rev-accurate-metadata"])
            elif _is_vague(text):
                self.add("usage-string-vague", "medium", f"{k} is too vague",
                         "Apple asks that permission text say clearly why the app needs the data, ideally with an "
                         "example. Vague text like \"needs access\" is a common 5.1.1 rejection, and a clear reason "
                         "gets more people to tap Allow.",
                         "Rewrite as: <App> uses <thing> to <specific purpose>, so you can <benefit>.", f, n, text,
                         ["rev-permission-strings"])
            if k not in used_keys and k != "NSUserTrackingUsageDescription" and not self.facts.get("expo_managed"):
                self.add("usage-string-unused", "low", f"{k} is set but no code was found that uses it",
                         "Permission text for a feature the app does not have can confuse App Review, which may ask "
                         "where the feature is. (A plugin may use it in a way Rankwell does not detect.)",
                         "If the feature was removed, delete the key; if a plugin needs it, keep it.", f, n)

    def rating_prompt(self) -> None:
        sites = []
        for lang in ("swift", "dart", "js"):
            sites += list(self.repo.grep(self.repo.code(lang), RATING_CALL[lang]))
        passive = list(self.repo.grep(self.repo.code("swift") + self.repo.code("dart") + self.repo.code("js"),
                                      RATING_PASSIVE))
        pkgs = [p for p in RATING_PACKAGES if p in self.deps]
        self.facts["rating_prompt"] = [f"{f}:{n}" for f, n, _ in sites]
        if not sites and not passive:
            where = self.deps[pkgs[0]] if pkgs else (None, None)
            self.add("rating-prompt-missing", "medium",
                     "No rating prompt" + (f" ({pkgs[0]} is installed but never called)" if pkgs else ""),
                     "Ratings count and average feed App Store search ranking and are the first trust signal in "
                     "search results. Without a prompt, few happy users ever rate; unhappy ones still review.",
                     "Call the system review prompt (SKStoreReviewController / StoreKit requestReview, Flutter "
                     "in_app_review, expo-store-review) right after the user completes a core action "
                     "successfully, e.g. after the 3rd saved item, not at launch.", where[0], where[1],
                     rules=["rr-prompt-timing", "rr-system-prompt"])
            return
        for f, n, _ in sites:
            lines = self.repo.text(f).splitlines()
            window = "\n".join(lines[max(0, n - 13): n + 6])
            near = "\n".join(lines[max(0, n - 7): n])
            base = f.rsplit("/", 1)[-1]
            if LAUNCH_FILE_RE.search(base) or (LAUNCH_CTX_RE.search(window) and not GUARD_RE.search(window)):
                self.add("rating-prompt-early", "medium", "Rating prompt may show at launch or during onboarding",
                         "People who have not used the app yet rarely rate it well, and the system shows the prompt "
                         "at most 3 times a year, so an early ask wastes one of them.",
                         "Move the call to just after a successful core action, gated by a counter (e.g. after "
                         "the 3rd completed task and at least 3 days after install).", f, n,
                         rules=["rr-prompt-timing"])
            elif ERROR_RE.search(near):
                self.add("rating-prompt-after-error", "medium", "Rating prompt is called next to error handling",
                         "If the prompt can appear right after something failed, the user is asked to rate at "
                         "their least happy moment.", "Call it only on the success path.", f, n,
                         rules=["rr-prompt-timing"])
            elif not GUARD_RE.search(window):
                self.add("rating-prompt-unguarded", "low", "Rating prompt has no visible condition",
                         "Without a counter or date check the prompt may be asked too often or too early.",
                         "Gate it: only after N successful core actions and not again within a few months.", f, n,
                         rules=["rr-prompt-timing"])
            if GATING_RE.search(self.repo.text(f)):
                self.add("rating-prompt-gated", "medium", "A custom \"do you like the app?\" question may filter "
                         "who gets the rating prompt",
                         "Apple asks apps to use its rating API and treats filtered or incentivised feedback as "
                         "review manipulation (Guideline 5.6.1); apps have been rejected for it.",
                         "Show the system prompt without a pre-question. Offer a separate, always-visible "
                         "\"Send feedback\" option instead.", f, n, rules=["rr-no-gating"])

    def display_name(self) -> None:
        name = None
        src: tuple[Any, str, int | None] | None = None
        for k in ("CFBundleDisplayName", "CFBundleName"):
            src = self.key(k)
            if src and str(src[0]).strip():
                name = str(src[0]).strip()
                break
        if self.expo and not name:
            name = self.expo.get("name")
        if name and "$(" in name:
            resolved = self._product_name()
            name = re.sub(r"\$\((PRODUCT_NAME|TARGET_NAME)[^)]*\)", resolved, name) if resolved else None
        self.facts["display_name"] = name
        localized = self._localized_display_names()
        self.facts["localized_display_names"] = localized
        if not name:
            return
        f, n = (src[1], src[2]) if src else (None, None)
        if self.listing_name and not _name_matches(name, self.listing_name):
            self.add("display-name-mismatch", "medium",
                     f"Home screen name \"{name}\" is not part of the App Store name \"{self.listing_name}\"",
                     "People search the App Store for the name they see on their home screen or hear from a "
                     "friend. If the store name does not contain it, those searches find someone else first, "
                     "and App Review asks that the store name and the installed name be similar (Guideline 2.3.8).",
                     "Make the home screen name the brand part of the store name (e.g. store: \"Habit Tracker - "
                     "Brand\", home screen: \"Brand\"), or rename one of them.", f, n, name,
                     ["meta-name-brand", "rev-name-match"])
        if len(name) > 12:
            self.add("display-name-long", "low", f"Home screen name \"{name}\" is {len(name)} characters",
                     "Names longer than about 12 characters are cut with … under the icon on the home screen.",
                     "Use a shorter CFBundleDisplayName (the brand only); the store name can stay longer.", f, n,
                     name)
        if self._app_langs() and not localized:
            self.add("display-name-not-localized", "low", "The app is localized but its name is not",
                     "If the store listing name is localized for a market, a matching home screen name keeps the "
                     "brand consistent from search to home screen.",
                     "Add CFBundleDisplayName to InfoPlist.strings (or InfoPlist.xcstrings) for the languages "
                     "where the store name changes.", f, n, rules=["loc-display-name"])

    def _product_name(self) -> str | None:
        for f in self.repo.named("project.pbxproj"):
            for m in re.finditer(r'PRODUCT_NAME\s*=\s*"?([^";$]+)"?;', self.repo.text(f)):
                v = m.group(1).strip()
                if v and "Tests" not in v:
                    return v
        return None

    def _localized_display_names(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for f in self.repo.named("InfoPlist.strings"):
            m = re.search(r"/([A-Za-z_-]+)\.lproj/", "/" + f)
            v = re.search(r'"?CFBundleDisplayName"?\s*=\s*"((?:[^"\\]|\\.)*)"', self.repo.text(f))
            if m and v:
                out[m.group(1)] = v.group(1)
        for f in self.repo.named("InfoPlist.xcstrings"):
            data = _load_json(self.repo.text(f)) or {}
            entry = (data.get("strings") or {}).get("CFBundleDisplayName") or {}
            for lang, loc in (entry.get("localizations") or {}).items():
                value = (loc.get("stringUnit") or {}).get("value")
                if value:
                    out[lang] = value
        return out

    def _app_langs(self) -> list[str]:
        """Languages the app's UI is translated into, beyond the base language."""
        langs: set[str] = set()
        for d in self.repo.dirs:
            base = d.rsplit("/", 1)[-1]
            if base.endswith(".lproj") and not is_test(d + "/x"):
                langs.add(base[:-6])
        for f in self.repo.ext(".xcstrings"):
            data = _load_json(self.repo.text(f)) or {}
            for entry in (data.get("strings") or {}).values():
                langs.update((entry or {}).get("localizations", {}).keys())
        for f in self.repo.ext(".arb"):
            m = re.search(r"(?:^|_)([a-z]{2,3}(?:_[A-Z]{2})?)\.arb$", f.rsplit("/", 1)[-1])
            if m:
                langs.add(m.group(1))
        for f in self.repo.ext(".json"):
            parts = f.split("/")
            if any(p in ("locales", "i18n", "translations", "lang", "langs", "locale") for p in parts[:-1]):
                stem = parts[-1][:-5]
                cand = stem if LANG_RE.match(stem) else parts[-2] if LANG_RE.match(parts[-2]) else None
                if cand:
                    langs.add(cand)
        langs.discard("Base")
        return sorted(lang for lang in langs if LANG_RE.match(lang) and not lang.lower().startswith("en"))

    def localizations(self) -> None:
        langs = self._app_langs()
        declared_entry = self.key("CFBundleLocalizations")
        declared = [str(x) for x in declared_entry[0]] if declared_entry and isinstance(declared_entry[0],
                                                                                         list) else []
        self.facts["localizations"] = langs
        self.facts["declared_localizations"] = declared
        if not langs:
            self.add("app-not-localized", "low", "The app is in one language only",
                     "Each App Store country can show a listing in its own language, and people search in their "
                     "own language. A translated listing works best when the app itself speaks that language too; "
                     "the product page lists the app's languages.",
                     "When a country shows interest (impressions, downloads), localize the listing first, then the "
                     "app's main screens.", rules=["loc-listing-first", "loc-pick-markets"])
            return
        if self.kind == "flutter":
            missing = [lang for lang in langs if lang.replace("_", "-") not in declared and lang not in declared]
            if missing and self.main_plists:
                f = self.main_plists[0]
                self.add("flutter-localizations-undeclared", "medium",
                         "Flutter translations are not declared in Info.plist",
                         "iOS and the App Store only know the languages listed in CFBundleLocalizations. Without "
                         "them, the product page shows English only and iOS may not switch the app's language.",
                         f"Add CFBundleLocalizations to {f} with: en, {', '.join(missing)}.", f,
                         _line_of(self.repo.text(f), "<dict>"), ", ".join(missing), ["loc-declare-languages"])

    def privacy_manifest(self) -> None:
        manifests = [f for f in self.repo.files if f.endswith("PrivacyInfo.xcprivacy")]
        self.facts["privacy_manifests"] = manifests
        rr = list(self.repo.grep(self.repo.code("swift"), REQUIRED_REASON_RE))
        if not manifests:
            if self.facts.get("expo_managed"):
                if not (self.expo.get("ios") or {}).get("privacyManifests"):
                    self.add("privacy-manifest-missing", "medium", "No privacy manifest settings in app.json",
                             "Apps that use certain system APIs (for example UserDefaults, which React Native "
                             "uses) must declare why in a privacy manifest, or uploads get flagged.",
                             "Add expo.ios.privacyManifests to app.json (see Expo's privacy manifest guide).",
                             "app.json", rules=["rev-privacy-manifest"])
                return
            if rr:
                f, n, _ = rr[0]
                self.add("privacy-manifest-missing", "high", "No privacy manifest (PrivacyInfo.xcprivacy)",
                         "The app's code uses an API that needs a declared reason (e.g. UserDefaults). App Store "
                         "Connect flags uploads that use such APIs without a privacy manifest that states why.",
                         "In Xcode: File > New > File > App Privacy, add it to the app target, and declare each "
                         "API category with its reason (UserDefaults: CA92.1 for data the app reads itself).",
                         f, n, rules=["rev-privacy-manifest"])
            elif self.kind == "react-native":
                self.add("privacy-manifest-missing", "high", "No privacy manifest (PrivacyInfo.xcprivacy)",
                         "React Native itself uses APIs that need a declared reason (UserDefaults, file timestamps, "
                         "boot time). Newer templates include ios/<App>/PrivacyInfo.xcprivacy.",
                         "Add PrivacyInfo.xcprivacy to the iOS app target with the reasons from the current React "
                         "Native template.", rules=["rev-privacy-manifest"])
            elif self.kind in ("native", "flutter"):
                self.add("privacy-manifest-missing", "low", "No privacy manifest for the app itself",
                         "SDKs ship their own manifests, but the app's own code and data collection should be "
                         "declared too; Apple checks manifests on upload.",
                         "Add PrivacyInfo.xcprivacy to the app target (File > New > File > App Privacy).",
                         rules=["rev-privacy-manifest"])
            return
        main = next((m for m in manifests if not is_test(m)), manifests[0])
        data = _load_plist((self.root / main).read_bytes()) or {}
        cats = {str(t.get("NSPrivacyAccessedAPIType")) for t in data.get("NSPrivacyAccessedAPITypes", []) or []
                if isinstance(t, dict)}
        if any("UserDefaults" in line for _, _, line in rr) and "NSPrivacyAccessedAPICategoryUserDefaults" not in cats:
            f, n, _ = next(x for x in rr if "UserDefaults" in x[2])
            self.add("privacy-manifest-reason", "medium", "Privacy manifest does not declare UserDefaults",
                     "The code uses UserDefaults, a required-reason API, but the manifest has no reason for it; "
                     "App Store Connect flags the upload.",
                     f"Add NSPrivacyAccessedAPICategoryUserDefaults with reason CA92.1 to {main}.", f, n,
                     rules=["rev-privacy-manifest"])
        if data.get("NSPrivacyTracking") and not data.get("NSPrivacyTrackingDomains"):
            self.add("privacy-manifest-tracking", "low", "Tracking is declared but no tracking domains are listed",
                     "With NSPrivacyTracking on, iOS blocks connections to tracking domains until the user allows "
                     "tracking; undeclared domains make the declaration incomplete.",
                     "List the tracking domains in NSPrivacyTrackingDomains, or set NSPrivacyTracking to false "
                     "if the app does not track.", main, rules=["rev-tracking"])
        projects = self.repo.named("project.pbxproj")
        if projects and not any("PrivacyInfo.xcprivacy" in self.repo.text(p) for p in projects):
            self.add("privacy-manifest-not-in-target", "medium", "PrivacyInfo.xcprivacy is not in the Xcode project",
                     "A manifest that is not part of the app target is not copied into the build, so Apple never "
                     "sees it.", "Drag it into Xcode and tick the app target.", main,
                     rules=["rev-privacy-manifest"])

    def deep_links(self) -> None:
        domains: list[str] = []
        ent_file = None
        for f in self.repo.ext(".entitlements"):
            data = _load_plist((self.root / f).read_bytes()) or {}
            ad = data.get("com.apple.developer.associated-domains") or []
            if ad:
                domains += [str(d) for d in ad]
                ent_file = ent_file or f
        domains += [str(d) for d in ((self.expo.get("ios") or {}).get("associatedDomains") or [])]
        schemes: list[str] = []
        url_types = self.key("CFBundleURLTypes")
        for t in (url_types[0] if url_types and isinstance(url_types[0], list) else []):
            if isinstance(t, dict):
                schemes += [str(s) for s in t.get("CFBundleURLSchemes", []) or []]
        if self.expo.get("scheme"):
            schemes.append(str(self.expo["scheme"]))
        schemes = [s for s in schemes if not s.startswith(("fb", "com.googleusercontent", "$("))]
        applinks = [d for d in domains if d.startswith("applinks:")]
        self.facts["associated_domains"] = domains
        self.facts["url_schemes"] = schemes
        if not applinks and not schemes:
            self.add("deep-links-missing", "low", "No universal links or URL scheme",
                     "Custom product pages and ads can send people to a specific screen only if the app handles "
                     "a link. Without one, every link lands on the home screen of the app.",
                     "Add Associated Domains (applinks:yourdomain.com) with an apple-app-site-association file on "
                     "the site, and route incoming links to screens.", rules=["cvr-cpp-deep-link"])
            return
        if not applinks:
            self.add("universal-links-missing", "low", "Only a custom URL scheme, no universal links",
                     "Custom schemes only work when the app is installed and cannot be shared as normal web links. "
                     "Universal links open the app when installed and the website otherwise.",
                     "Add Associated Domains with applinks:yourdomain.com and host apple-app-site-association.",
                     rules=["cvr-cpp-deep-link"])
        handled = any(True for lang in ("swift", "dart", "js")
                      for _ in self.repo.grep(self.repo.code(lang), LINK_HANDLER[lang]))
        if applinks and not handled and not self.key("FlutterDeepLinkingEnabled"):
            self.add("deep-links-unhandled", "low", "Universal links are declared but no code handles them",
                     "The link opens the app but does not take the user to the promised screen, which loses the "
                     "point of a custom product page link.",
                     "Handle incoming URLs (onOpenURL / continue userActivity, app_links, Linking) and route them.",
                     ent_file, rules=["cvr-cpp-deep-link"])

    def app_icon(self) -> None:
        sets = [d for d in self.repo.dirs if d.endswith(".appiconset")]
        composer = [d for d in self.repo.dirs if d.endswith(".icon") and "AppIcon" in d]
        self.facts["app_icons"] = sets + composer
        if self.facts.get("expo_managed"):
            icon = self.expo.get("icon") or (self.expo.get("ios") or {}).get("icon")
            if not icon or not (self.root / str(icon)).is_file():
                self.add("icon-missing", "high", "No app icon file found in app.json",
                         "The 1024 px App Store icon is required and is the first thing people see in search.",
                         "Set expo.icon to a 1024x1024 PNG without transparency.", "app.json",
                         rules=["cvr-icon"])
            return
        if not sets and not composer:
            if self.kind != "unknown":
                self.add("icon-missing", "high", "No app icon set found",
                         "An App Store icon (1024 x 1024) is required to submit, and the icon is the first thing "
                         "people judge in search results.",
                         "Add an AppIcon set in Assets.xcassets with a 1024 x 1024 PNG.", rules=["cvr-icon"])
            return
        if composer and not sets:
            return
        main = next((s for s in sets if s.rsplit("/", 1)[-1] == "AppIcon.appiconset"), sets[0])
        contents = f"{main}/Contents.json"
        data = _load_json(self.repo.text(contents)) or {}
        images = [i for i in data.get("images", []) if isinstance(i, dict)]
        marketing = [i for i in images if not i.get("appearances") and (
            i.get("idiom") == "ios-marketing" or (i.get("size") == "1024x1024" and i.get("idiom") in (
                "universal", "ios", "iphone")))]
        if not marketing or not marketing[0].get("filename"):
            self.add("icon-1024-missing", "high", "The 1024 x 1024 App Store icon is missing",
                     "App Store Connect needs the large icon from the build; uploads without it fail validation.",
                     f"Add a 1024 x 1024 PNG (no transparency) to {main}.", contents, rules=["cvr-icon"])
        else:
            png = self.root / main / str(marketing[0]["filename"])
            if not png.is_file():
                self.add("icon-1024-missing", "high", "The 1024 px icon file named in Contents.json does not exist",
                         "The build will have no App Store icon.", f"Add {png.name} to {main}.", contents,
                         rules=["cvr-icon"])
            else:
                head = png.read_bytes()[:33]
                if head[:8] == b"\x89PNG\r\n\x1a\n":
                    w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
                    if (w, h) != (1024, 1024):
                        self.add("icon-1024-size", "high", f"App Store icon is {w} x {h}, not 1024 x 1024",
                                 "App Store Connect rejects a wrong-size marketing icon.",
                                 "Export the icon at exactly 1024 x 1024.", f"{main}/{png.name}", rules=["cvr-icon"])
                    if head[25] in (4, 6):
                        self.add("icon-alpha", "high", "App Store icon has an alpha channel",
                                 "The 1024 px icon must not be transparent or have an alpha channel; the upload "
                                 "fails validation.",
                                 "Re-export as PNG without alpha (e.g. flatten onto the background colour).",
                                 f"{main}/{png.name}", rules=["cvr-icon"])
        sized = [i for i in images if not i.get("appearances") and i.get("size") not in (None, "1024x1024")]
        empty = [i for i in sized if not i.get("filename")]
        if sized and empty:
            self.add("icon-slots-empty", "low", f"{len(empty)} icon sizes have no image",
                     "Empty slots make Xcode warn and smaller sizes fall back to scaled images that look blurry.",
                     "Fill every slot, or switch the set to a single 1024 px size (Xcode 14+).", contents,
                     rules=["cvr-icon"])

    def store_products(self) -> None:
        sdks = sorted({v for k, v in IAP_PACKAGES.items() if k in self.deps})
        config_ids: set[str] = set()
        for f in self.repo.ext(".storekit"):
            data = _load_json(self.repo.text(f)) or {}
            for p in data.get("products", []) or []:
                config_ids.add(str(p.get("productID")))
            for p in data.get("nonRenewingSubscriptions", []) or []:
                config_ids.add(str(p.get("productID")))
            for g in data.get("subscriptionGroups", []) or []:
                for p in g.get("subscriptions", []) or []:
                    config_ids.add(str(p.get("productID")))
        code_ids: dict[str, str] = {}
        uses_storekit = bool(sdks)
        for lang in ("swift", "dart", "js"):
            for f in self.repo.code(lang):
                text = self.repo.text(f)
                if not IAP_CODE_RE.search(text):
                    continue
                uses_storekit = True
                for n, line in enumerate(text.splitlines(), start=1):
                    if COMMENT_RE.match(line) or "import" in line:
                        continue
                    for m in PRODUCT_ID_RE.finditer(line):
                        pid = m.group(1)
                        if (PRODUCT_WORD_RE.search(pid) and not FILE_LIKE_RE.search(pid)
                                and not pid.startswith(("com.apple.", "http", "package:", "NS", "UI"))):
                            code_ids.setdefault(pid, f"{f}:{n}")
        self.facts["iap_sdks"] = sdks
        self.facts["product_ids"] = sorted(set(code_ids) | config_ids - {"None"})
        if not uses_storekit:
            return
        ids = sorted(code_ids)
        if ids:
            first = code_ids[ids[0]].rsplit(":", 1)
            self.add("product-ids", "low", f"{len(ids)} in-app purchase id(s) in the code",
                     "Each id must exist in App Store Connect with the exact same spelling and be ready to submit. "
                     "A typo shows an empty paywall, which App Review rejects (Guideline 2.1).",
                     "Compare these ids with App Store Connect > In-App Purchases / Subscriptions.", first[0],
                     int(first[1]), ", ".join(ids[:12]), ["rev-iap-ready"])
        stray = sorted(set(code_ids) - config_ids) if config_ids else []
        if stray:
            self.add("product-ids-not-in-storekit-config", "low",
                     "Product ids in code are missing from the StoreKit configuration file",
                     "Local testing will not load these products, so paywall bugs can slip through to review.",
                     "Add them to the .storekit file or fix the spelling.", code_ids[stray[0]].rsplit(":", 1)[0],
                     evidence=", ".join(stray[:12]), rules=["rev-iap-ready"])
        if "RevenueCat" in sdks and not ids:
            self.facts["product_ids_note"] = "Product ids live in RevenueCat offerings, not in code."

    def placeholders(self) -> None:
        shown = 0
        files = [f for f in self.repo.ext(".strings", ".xcstrings", ".arb") if not is_test(f)]
        files += [f for f in self.repo.ext(".json") if any(p in ("locales", "i18n", "translations", "lang", "l10n")
                                                          for p in f.split("/")[:-1])]
        hits = list(self.repo.grep(files, PLACEHOLDER_RE))
        literal = re.compile(r"[\"'][^\"'\n]*(lorem ipsum|\bTODO\b|\bFIXME\b|\bTBD\b)[^\"'\n]*[\"']", re.I)
        for lang in ("swift", "dart", "js"):
            for f, n, line in self.repo.grep(self.repo.code(lang), literal):
                code = re.sub(r"\s//\s.*$", "", line)
                m = literal.search(code)
                if m and not LOG_RE.search(line) and (m.group(1).lower() == "lorem ipsum" or UI_CTX_RE.search(code)):
                    hits.append((f, n, line))
        for f in self.main_plists:
            hits += list(self.repo.grep([f], PLACEHOLDER_RE))
        for f, n, line in hits:
            if shown >= 10:
                break
            shown += 1
            self.add("placeholder-text", "medium", "Placeholder text in a user-facing string",
                     "App Review rejects apps with placeholder content (Guideline 2.1), and reviewers and users "
                     "who see it judge the app as unfinished.", "Replace it with the final text.", f, n,
                     line.strip()[:120], ["rev-complete"])
        if len(hits) > shown:
            self.add("placeholder-text-more", "low", f"{len(hits) - shown} more placeholder strings",
                     "Same as above.", "Search the project for TODO, FIXME, TBD and lorem ipsum in strings.")

    def tracking(self) -> None:
        ads = sorted({v for k, v in ADS_SDKS.items() if k in self.deps})
        analytics = sorted({v for k, v in ANALYTICS_SDKS.items() if k in self.deps})
        for _f, _, line in self.repo.grep(self.repo.code("swift"), r"^\s*import\s+\w+"):
            mod = line.split()[-1]
            if mod in SWIFT_SDK_IMPORTS:
                (analytics if "Analytics" in mod else ads).append(SWIFT_SDK_IMPORTS[mod])
        ads, analytics = sorted(set(ads)), sorted(set(analytics))
        self.facts["ads_sdks"], self.facts["analytics_sdks"] = ads, analytics
        att = next(p for p in PERMS if p.keys == ("NSUserTrackingUsageDescription",))
        calls = [(f, n) for lang in ("swift", "dart", "js") for f, n, _ in self.repo.grep(
            self.repo.code(lang), getattr(att, lang)) if getattr(att, lang)]
        has_key = self.key("NSUserTrackingUsageDescription") is not None
        self.facts["att_prompt"] = [f"{f}:{n}" for f, n in calls]
        if ads and not calls:
            where = next((self.deps[k] for k, v in ADS_SDKS.items() if v == ads[0] and k in self.deps), (None, None))
            self.add("att-missing", "medium", f"Ads or attribution SDK ({', '.join(ads)}) but no tracking prompt",
                     "If the SDK tracks users across apps (personalised ads, install attribution), the app must ask "
                     "with App Tracking Transparency first. App Review compares your App Privacy answers with the "
                     "app and rejects tracking without the prompt (Guideline 5.1.2).",
                     "Either request ATT (with NSUserTrackingUsageDescription) before the SDK starts, or configure "
                     "the SDK for non-personalised use and answer App Privacy to match.", where[0], where[1],
                     rules=["rev-tracking"])
        if has_key and not calls and not ads:
            entry = self.key("NSUserTrackingUsageDescription")
            f, n = (entry[1], entry[2]) if entry else (None, None)
            self.add("att-unused", "low", "Tracking permission text is set but the app never asks",
                     "App Review looks for the ATT prompt when the text is present and may reject when it cannot "
                     "find it (Guideline 2.1).", "Remove NSUserTrackingUsageDescription if the app does not track.",
                     f, n, rules=["rev-tracking"])
        for f, n in calls:
            lines = self.repo.text(f).splitlines()
            before = "\n".join(lines[max(0, n - 20): n])
            if "didFinishLaunching" in before:
                self.add("att-at-launch", "medium", "Tracking prompt is requested in didFinishLaunching",
                         "iOS shows the ATT prompt only while the app is active; asked during launch it may never "
                         "appear, and App Review then rejects because it cannot find it (Guideline 2.1).",
                         "Request it once the first screen is visible (applicationDidBecomeActive / onAppear), "
                         "and before any tracking SDK starts.", f, n, rules=["rev-tracking"])
        if analytics and not ads:
            self.add("analytics-privacy", "low", f"Analytics SDK found ({', '.join(analytics)})",
                     "Data the SDK collects must appear in the App Privacy answers in App Store Connect. No ATT "
                     "prompt is needed unless the data is linked with other companies' data for ads.",
                     "Check App Privacy covers what this SDK collects (usage data, identifiers).",
                     rules=["rev-tracking"])

    def export_compliance(self) -> None:
        if self.facts.get("expo_managed") or not (self.main_plists or self.keys):
            return
        if self.key("ITSAppUsesNonExemptEncryption") is None:
            f = self.main_plists[0] if self.main_plists else None
            self.add("export-compliance", "low", "Encryption question is not answered in Info.plist",
                     "Without ITSAppUsesNonExemptEncryption, every build waits for the export compliance question "
                     "in App Store Connect before it can be tested or submitted.",
                     "If the app only uses standard HTTPS/system encryption, add ITSAppUsesNonExemptEncryption = NO.",
                     f, rules=["rel-export-compliance"])

    def run(self) -> None:
        self.load()
        for check in (self.permissions, self.rating_prompt, self.display_name, self.localizations,
                      self.privacy_manifest, self.deep_links, self.app_icon, self.store_products, self.placeholders,
                      self.tracking, self.export_compliance):
            check()


def _is_vague(text: str) -> bool:
    t = text.strip()
    if not t or PLACEHOLDER_RE.search(t) or GENERIC_PURPOSE_RE.search(t):
        return True
    rest = VAGUE_STRIP_RE.sub(" ", t)
    return len(t) < 20 or not PURPOSE_RE.search(rest)


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def _name_matches(display: str, listing: str) -> bool:
    d, listing_words = _words(display), _words(listing)
    if not d:
        return True
    joined_d, joined_l = "".join(d), "".join(listing_words)
    return joined_d in joined_l or joined_l in joined_d or all(w in listing_words for w in d)


def audit(repo: Path | str, listing_name: str | None = None) -> dict[str, Any]:
    """Scan an app repo and return findings (worst first), facts found on the way, and counts per severity."""
    root = Path(repo).expanduser()
    if not root.is_dir():
        raise RankwellError(f"Not a folder: {root}")
    a = _Audit(root, listing_name)
    a.run()
    order = {s: i for i, s in enumerate(SEVERITIES)}
    a.findings.sort(key=lambda f: (order[f.severity], f.check, f.file or "", f.line or 0))
    counts = {s: sum(1 for f in a.findings if f.severity == s) for s in SEVERITIES}
    return {"repo": str(root), "kind": a.kind, "findings": [asdict(f) | {"where": f.where()} for f in a.findings],
            "counts": counts, "facts": a.facts, "files_scanned": len(a.repo.files)}
