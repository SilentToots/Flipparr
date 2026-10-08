// Flipparr Reader's own native plugin (MIT, like the rest of Flipparr), so the
// app needs no third-party plugin for what only the platform can do:
//
// - the Keychain, for the server's address and its sign-in tokens
//   (src/reader-app/secure-store.js);
// - (Phase 2, next) fetching a page straight to a file, so images never
//   cross the JavaScript bridge; (Phase 3) background downloads.
//
// Registered by FlipparrViewController below, which Main.storyboard names.
import Capacitor
import Foundation
import Security

@objc(FlipparrNativePlugin)
public class FlipparrNativePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "FlipparrNativePlugin"
    public let jsName = "FlipparrNative"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "keychainGet", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "keychainSet", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "keychainRemove", returnType: CAPPluginReturnPromise),
    ]

    private let service = Bundle.main.bundleIdentifier ?? "com.silenttoots.flipparr.view"

    private func item(_ key: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: key]
    }

    private func key(_ call: CAPPluginCall) -> String? {
        guard let key = call.getString("key"), !key.isEmpty else {
            call.reject("A key is needed")
            return nil
        }
        return key
    }

    @objc func keychainGet(_ call: CAPPluginCall) {
        guard let key = key(call) else { return }
        var query = item(key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var found: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &found)
        if status == errSecItemNotFound {
            call.resolve(["value": NSNull()])
            return
        }
        guard status == errSecSuccess, let data = found as? Data, let value = String(data: data, encoding: .utf8) else {
            call.reject("The Keychain could not be read (\(status))")
            return
        }
        call.resolve(["value": value])
    }

    @objc func keychainSet(_ call: CAPPluginCall) {
        guard let key = key(call) else { return }
        guard let value = call.getString("value") else {
            call.reject("A value is needed")
            return
        }
        // After first unlock, so a background download can read the token
        // while the iPad is locked; this device only, so it is never in a
        // backup or restored onto another device.
        let attributes: [String: Any] = [
            kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        var status = SecItemUpdate(item(key) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(item(key).merging(attributes) { $1 } as CFDictionary, nil)
        }
        if status == errSecSuccess {
            call.resolve()
        } else {
            call.reject("The Keychain could not be written (\(status))")
        }
    }

    @objc func keychainRemove(_ call: CAPPluginCall) {
        guard let key = key(call) else { return }
        let status = SecItemDelete(item(key) as CFDictionary)
        if status == errSecSuccess || status == errSecItemNotFound {
            call.resolve()
        } else {
            call.reject("The Keychain could not be cleared (\(status))")
        }
    }
}

/// The app's web view controller: Capacitor's, plus the app's own plugin.
class FlipparrViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(FlipparrNativePlugin())
    }
}
