// Flipparr Reader's own native plugin (MIT, like the rest of Flipparr), so the
// app needs no third-party plugin for what only the platform can do:
//
// - the Keychain, for the server's address and its sign-in tokens
//   (src/reader-app/secure-store.js);
// - fetching an image straight to a file, so pages never cross the
//   JavaScript bridge (src/reader-app/images.js), and keeping the folder
//   they live in to a size; (Phase 3) background downloads.
//
// Everything the app keeps is under Library/flipparr, which iOS does not
// purge the way it does Caches, and which is excluded from iCloud and
// device backups: pages can be downloaded again, and 2 GB of them in every
// backup would be wrong (Apple, "isExcludedFromBackupKey").
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
        CAPPluginMethod(name: "fetchToFile", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "trimFolder", returnType: CAPPluginReturnPromise),
    ]

    /// Library/flipparr, made on first use and kept out of backups. Apple
    /// notes the flag can be lost when files are moved or replaced, so it is
    /// set again every time the folder is asked for.
    private func root() throws -> URL {
        let library = try FileManager.default.url(for: .libraryDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        var folder = library.appendingPathComponent("flipparr", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try folder.setResourceValues(values)
        return folder
    }

    /// A path inside Library/flipparr, refusing one that climbs out of it.
    private func inside(_ relative: String) throws -> URL {
        let base = try root().standardizedFileURL
        let target = base.appendingPathComponent(relative).standardizedFileURL
        guard target.path.hasPrefix(base.path + "/") else {
            throw NSError(domain: "FlipparrNative", code: 1, userInfo: [NSLocalizedDescriptionKey: "That path is outside the app's folder"])
        }
        return target
    }

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

extension FlipparrNativePlugin {
    /// GET `url` into `path` (relative to Library/flipparr) unless the file
    /// is already there; `headers` go with the request (the app adds its
    /// token only for its own server). Resolves {path, bytes, status,
    /// cached}; a non-2xx answer rejects with the status as the code.
    @objc func fetchToFile(_ call: CAPPluginCall) {
        guard let address = call.getString("url"), let url = URL(string: address),
              let relative = call.getString("path"), !relative.isEmpty else {
            call.reject("A url and a path are needed")
            return
        }
        let destination: URL
        do { destination = try inside(relative) } catch { call.reject(error.localizedDescription); return }
        if let size = (try? destination.resourceValues(forKeys: [.fileSizeKey]))?.fileSize {
            // Touched, so trimming keeps what is being read.
            try? FileManager.default.setAttributes([.modificationDate: Date()], ofItemAtPath: destination.path)
            call.resolve(["path": destination.path, "bytes": size, "status": 200, "cached": true])
            return
        }
        var request = URLRequest(url: url)
        for (name, value) in call.getObject("headers") ?? [:] {
            if let text = value as? String { request.setValue(text, forHTTPHeaderField: name) }
        }
        URLSession.shared.downloadTask(with: request) { temporary, response, error in
            if let error = error {
                call.reject(error.localizedDescription, "network")
                return
            }
            guard let http = response as? HTTPURLResponse, let temporary = temporary else {
                call.reject("No answer", "network")
                return
            }
            guard (200..<300).contains(http.statusCode) else {
                call.reject("The server answered \(http.statusCode)", String(http.statusCode))
                return
            }
            do {
                try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
                if FileManager.default.fileExists(atPath: destination.path) {
                    try FileManager.default.removeItem(at: destination)
                }
                try FileManager.default.moveItem(at: temporary, to: destination)
                let size = (try? destination.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
                call.resolve(["path": destination.path, "bytes": size, "status": http.statusCode, "cached": false])
            } catch {
                call.reject("The file could not be kept: \(error.localizedDescription)")
            }
        }.resume()
    }

    /// Delete the least recently used files in `path` (relative) until it
    /// holds no more than `maxBytes`. Resolves {bytes, removed}.
    @objc func trimFolder(_ call: CAPPluginCall) {
        guard let relative = call.getString("path"), !relative.isEmpty else {
            call.reject("A path is needed")
            return
        }
        let limit = call.getInt("maxBytes") ?? 0
        do {
            let folder = try inside(relative)
            let keys: [URLResourceKey] = [.fileSizeKey, .contentModificationDateKey, .isRegularFileKey]
            let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: keys)) ?? []
            var entries = files.compactMap { file -> (URL, Int, Date)? in
                guard let values = try? file.resourceValues(forKeys: Set(keys)), values.isRegularFile == true else { return nil }
                return (file, values.fileSize ?? 0, values.contentModificationDate ?? .distantPast)
            }
            entries.sort { $0.2 < $1.2 }
            var total = entries.reduce(0) { $0 + $1.1 }
            var removed = 0
            for (file, size, _) in entries where total > limit {
                if (try? FileManager.default.removeItem(at: file)) != nil {
                    total -= size
                    removed += 1
                }
            }
            call.resolve(["bytes": total, "removed": removed])
        } catch {
            call.reject(error.localizedDescription)
        }
    }
}

/// The app's web view controller: Capacitor's, plus the app's own plugin.
class FlipparrViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(FlipparrNativePlugin())
    }
}
