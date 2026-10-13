// Flipparr View's own native plugin (MIT, like the rest of Flipparr), so the
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

/// Library/flipparr, made on first use and kept out of backups. Apple notes
/// the flag can be lost when files are moved or replaced, so it is set again
/// every time the folder is asked for.
func flipparrRoot() throws -> URL {
    let library = try FileManager.default.url(for: .libraryDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
    var folder = library.appendingPathComponent("flipparr", isDirectory: true)
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    var values = URLResourceValues()
    values.isExcludedFromBackup = true
    try folder.setResourceValues(values)
    return folder
}

/// A path inside Library/flipparr, refusing one that climbs out of it.
func flipparrPath(_ relative: String) throws -> URL {
    let base = try flipparrRoot().standardizedFileURL
    let target = base.appendingPathComponent(relative).standardizedFileURL
    guard target.path.hasPrefix(base.path + "/") else {
        throw NSError(domain: "FlipparrNative", code: 1, userInfo: [NSLocalizedDescriptionKey: "That path is outside the app's folder"])
    }
    return target
}

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
        CAPPluginMethod(name: "enqueue", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "cancel", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "transfers", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "unpack", returnType: CAPPluginReturnPromise),
    ]

    private func inside(_ relative: String) throws -> URL { try flipparrPath(relative) }

    override public func load() {
        // The downloader tells this plugin what finished; events that came
        // while no page was listening (a relaunch in the background) are
        // kept until one is.
        FlipparrDownloads.shared.plugin = self
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

// MARK: - Downloads that carry on in the background

extension FlipparrNativePlugin {
    /// Download `url` to `path` (relative to Library/flipparr) in the
    /// background session: it carries on while the app is suspended, and iOS
    /// relaunches the app to finish it. `id` names it in the events
    /// (`transferDone`, `transferFailed`). Resolves at once.
    @objc func enqueue(_ call: CAPPluginCall) {
        guard let id = call.getString("id"), let address = call.getString("url"), let url = URL(string: address),
              let relative = call.getString("path") else {
            call.reject("An id, a url and a path are needed")
            return
        }
        do { _ = try inside(relative) } catch { call.reject(error.localizedDescription); return }
        var headers: [String: String] = [:]
        for (name, value) in call.getObject("headers") ?? [:] {
            if let text = value as? String { headers[name] = text }
        }
        // A comic's package is unpacked into this folder as it lands.
        let unpack = call.getString("unpack")
        if let unpack = unpack {
            do { _ = try inside(unpack) } catch { call.reject(error.localizedDescription); return }
        }
        FlipparrDownloads.shared.enqueue(id: id, url: url, relative: relative, headers: headers, unpack: unpack)
        call.resolve()
    }

    /// Unpack a package left on disk (the app was stopped as it landed) into
    /// `folder`, and remove it. Resolves {pages}.
    @objc func unpack(_ call: CAPPluginCall) {
        guard let relative = call.getString("path"), let folder = call.getString("folder") else {
            call.reject("A path and a folder are needed")
            return
        }
        DispatchQueue.global(qos: .utility).async {
            do {
                let package = try self.inside(relative)
                let pages = try ComicPackage.unpack(package, into: try self.inside(folder))
                try? FileManager.default.removeItem(at: package)
                call.resolve(["pages": pages])
            } catch {
                call.reject(error.localizedDescription)
            }
        }
    }

    @objc func cancel(_ call: CAPPluginCall) {
        let prefix = call.getString("prefix") ?? ""
        FlipparrDownloads.shared.cancel(prefix: prefix) { cancelled in call.resolve(["cancelled": cancelled]) }
    }

    /// What the background session is still working on: [{id, path}].
    @objc func transfers(_ call: CAPPluginCall) {
        FlipparrDownloads.shared.transfers { list in call.resolve(["transfers": list]) }
    }
}

/// The one background URLSession. A singleton, not the plugin's: iOS may
/// relaunch the app in the background to deliver finished downloads before
/// any web page exists, and the files are moved into place here either way.
final class FlipparrDownloads: NSObject, URLSessionDownloadDelegate {
    static let shared = FlipparrDownloads()
    static let identifier = (Bundle.main.bundleIdentifier ?? "com.silenttoots.flipparr.view") + ".downloads"

    weak var plugin: FlipparrNativePlugin? { didSet { flush() } }
    /// Set by the AppDelegate when iOS relaunches the app for this session.
    var backgroundCompletion: (() -> Void)?
    private var waiting: [(String, [String: Any])] = []

    lazy var session: URLSession = {
        let configuration = URLSessionConfiguration.background(withIdentifier: FlipparrDownloads.identifier)
        configuration.sessionSendsLaunchEvents = true
        // Downloaded because someone asked: not left for when iOS thinks best.
        configuration.isDiscretionary = false
        return URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    }()

    /// One transfer: its id in the events, where it lands, the server it may
    /// send the token to, the folder a package is unpacked into, and how many
    /// times it has been asked again (the server busy) or resumed.
    private struct Job: Codable {
        let id: String
        let path: String
        let host: String
        var unpack: String? = nil
        var waits: Int? = nil
        var resumes: Int? = nil
    }

    /// The server packs a comic before it sends it, and says how long to
    /// wait meanwhile (503 + Retry-After). Asked again then, by iOS, with the
    /// app asleep; this many times at most (about an hour at a minute each).
    private static let waitsMax = 60
    /// A transfer that drops part way resumes from the bytes it has, this
    /// many times (the server's ETag and byte ranges make that possible).
    private static let resumesMax = 5

    func enqueue(id: String, url: URL, relative: String, headers: [String: String], unpack: String? = nil) {
        var request = URLRequest(url: url)
        headers.forEach { request.setValue($0.value, forHTTPHeaderField: $0.key) }
        let task = session.downloadTask(with: request)
        describe(task, Job(id: id, path: relative, host: url.host ?? "", unpack: unpack))
        task.resume()
    }

    private func describe(_ task: URLSessionTask, _ job: Job) {
        task.taskDescription = String(data: (try? JSONEncoder().encode(job)) ?? Data(), encoding: .utf8)
    }

    private func job(_ task: URLSessionTask) -> Job? {
        guard let text = task.taskDescription, let data = text.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(Job.self, from: data)
    }

    func cancel(prefix: String, done: @escaping (Int) -> Void) {
        session.getAllTasks { tasks in
            let matching = tasks.filter { self.job($0)?.id.hasPrefix(prefix) == true }
            matching.forEach { $0.cancel() }
            done(matching.count)
        }
    }

    func transfers(done: @escaping ([[String: Any]]) -> Void) {
        session.getAllTasks { tasks in
            done(tasks.compactMap { task in self.job(task).map { ["id": $0.id, "path": $0.path] } })
        }
    }

    private func emit(_ name: String, _ data: [String: Any]) {
        DispatchQueue.main.async {
            if let plugin = self.plugin { plugin.notifyListeners(name, data: data, retainUntilConsumed: true) }
            else { self.waiting.append((name, data)) }
        }
    }

    private func flush() {
        DispatchQueue.main.async {
            guard let plugin = self.plugin else { return }
            let pending = self.waiting
            self.waiting.removeAll()
            pending.forEach { plugin.notifyListeners($0.0, data: $0.1, retainUntilConsumed: true) }
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        guard var job = job(downloadTask) else { return }
        let response = downloadTask.response as? HTTPURLResponse
        let status = response?.statusCode ?? 0
        // Still being packed: asked again when the server says, by iOS, so
        // it goes on with the app asleep. Not a failure.
        if (status == 503 || status == 429), (job.waits ?? 0) < FlipparrDownloads.waitsMax,
           let request = downloadTask.originalRequest {
            let after = Double(response?.value(forHTTPHeaderField: "Retry-After") ?? "") ?? 10
            let again = session.downloadTask(with: request)
            again.earliestBeginDate = Date().addingTimeInterval(min(max(after, 2), 300))
            job.waits = (job.waits ?? 0) + 1
            describe(again, job)
            again.resume()
            emit("transferWaiting", ["id": job.id, "seconds": after])
            return
        }
        guard (200..<300).contains(status) else {
            emit("transferFailed", ["id": job.id, "status": status, "error": "The server answered \(status)"])
            return
        }
        if let unpack = job.unpack {
            // Unpacked now, inside this callback: the temporary file is gone
            // the moment it returns.
            do {
                let pages = try ComicPackage.unpack(location, into: try flipparrPath(unpack))
                let size = (try? location.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
                emit("transferDone", ["id": job.id, "pages": pages, "bytes": size, "status": status])
            } catch {
                emit("transferFailed", ["id": job.id, "status": status, "error": error.localizedDescription])
            }
            return
        }
        do {
            let destination = try flipparrPath(job.path)
            try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
            // Moved now, inside this callback: the temporary file is gone
            // the moment it returns.
            try FileManager.default.moveItem(at: location, to: destination)
            let size = (try? destination.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
            emit("transferDone", ["id": job.id, "path": destination.path, "bytes": size, "status": status])
        } catch {
            emit("transferFailed", ["id": job.id, "status": status, "error": error.localizedDescription])
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let error = error, var job = job(task) else { return }
        let nsError = error as NSError
        let cancelled = nsError.code == NSURLErrorCancelled
        // Dropped part way (the network went, the app was closed): resumed
        // from the bytes already here, as a byte range of the same file.
        if !cancelled, (job.resumes ?? 0) < FlipparrDownloads.resumesMax,
           let data = nsError.userInfo[NSURLSessionDownloadTaskResumeData] as? Data {
            let again = session.downloadTask(withResumeData: data)
            job.resumes = (job.resumes ?? 0) + 1
            describe(again, job)
            again.resume()
            return
        }
        emit("transferFailed", ["id": job.id, "status": 0, "error": error.localizedDescription, "network": !cancelled, "cancelled": cancelled])
    }

    /// How far a transfer has come, for its ring: at most every half second
    /// or every 2%, so a big package does not flood the bridge.
    private var progressSaid: [Int: (at: Date, fraction: Double)] = [:]
    private let progressLock = NSLock()

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        guard let job = job(downloadTask), job.unpack != nil else { return }
        let expected = totalBytesExpectedToWrite > 0 ? totalBytesExpectedToWrite : 0
        let fraction = expected > 0 ? Double(totalBytesWritten) / Double(expected) : 0
        progressLock.lock()
        let last = progressSaid[downloadTask.taskIdentifier]
        let due = last == nil || Date().timeIntervalSince(last!.at) >= 0.5 || fraction - last!.fraction >= 0.02
        if due { progressSaid[downloadTask.taskIdentifier] = (Date(), fraction) }
        progressLock.unlock()
        if due {
            emit("transferProgress", ["id": job.id, "written": totalBytesWritten, "expected": expected])
        }
    }

    /// The token goes only to the household's server: a redirect to any
    /// other host is followed without it.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        var next = request
        if let job = job(task), request.url?.host != job.host {
            next.setValue(nil, forHTTPHeaderField: "Authorization")
            next.setValue(nil, forHTTPHeaderField: "Flipparr-Device")
        }
        completionHandler(next)
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        DispatchQueue.main.async {
            self.backgroundCompletion?()
            self.backgroundCompletion = nil
        }
    }
}

/// A comic as the server packs it for downloading (app.py
/// `build_offline_package`): a zip of its pages, stored rather than
/// compressed (JPEGs do not compress), named 0000.jpg, 0001.jpg... in reading
/// order. Read here without a library: the central directory says where each
/// page's bytes are, and they are copied out as they are.
enum ComicPackage {
    struct Problem: LocalizedError {
        let reason: String
        var errorDescription: String? { reason }
    }

    /// Unpack `package` into `folder` as {index}.jpg, each written beside its
    /// place and moved in; returns how many pages there were.
    static func unpack(_ package: URL, into folder: URL) throws -> Int {
        let handle = try FileHandle(forReadingFrom: package)
        defer { try? handle.close() }
        let size = try handle.seekToEnd()
        // The end of central directory record: within the last 64 KB + 22 bytes.
        let tail = min(size, 65_557)
        try handle.seek(toOffset: size - tail)
        let end = try handle.read(upToCount: Int(tail)) ?? Data()
        guard let at = lastIndex(of: [0x50, 0x4b, 0x05, 0x06], in: end) else { throw Problem(reason: "The download is not a comic package") }
        let count = Int(le16(end, at + 10))
        var offset = UInt64(le32(end, at + 16))
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var pages = 0
        for _ in 0..<count {
            try handle.seek(toOffset: offset)
            guard let header = try handle.read(upToCount: 46), header.count == 46, le32(header, 0) == 0x02014b50 else {
                throw Problem(reason: "The comic package is damaged")
            }
            let method = le16(header, 10)
            let length = UInt64(le32(header, 20))
            let nameLength = Int(le16(header, 28)), extraLength = Int(le16(header, 30)), commentLength = Int(le16(header, 32))
            let local = UInt64(le32(header, 42))
            let nameData = try handle.read(upToCount: nameLength) ?? Data()
            offset += 46 + UInt64(nameLength + extraLength + commentLength)
            guard method == 0, let name = String(data: nameData, encoding: .utf8),
                  name.hasSuffix(".jpg"), let index = Int(name.dropLast(4)), index >= 0 else {
                throw Problem(reason: "The comic package holds something other than pages")
            }
            try handle.seek(toOffset: local)
            guard let localHeader = try handle.read(upToCount: 30), localHeader.count == 30, le32(localHeader, 0) == 0x04034b50 else {
                throw Problem(reason: "The comic package is damaged")
            }
            try handle.seek(toOffset: local + 30 + UInt64(le16(localHeader, 26)) + UInt64(le16(localHeader, 28)))
            guard let body = try handle.read(upToCount: Int(length)), body.count == Int(length) else {
                throw Problem(reason: "The comic package is cut short")
            }
            let target = folder.appendingPathComponent("\(index).jpg")
            let partial = folder.appendingPathComponent(".\(index).jpg.partial")
            try body.write(to: partial, options: .atomic)
            if FileManager.default.fileExists(atPath: target.path) { try FileManager.default.removeItem(at: target) }
            try FileManager.default.moveItem(at: partial, to: target)
            pages += 1
        }
        return pages
    }

    private static func le16(_ data: Data, _ at: Int) -> UInt16 {
        UInt16(data[data.startIndex + at]) | UInt16(data[data.startIndex + at + 1]) << 8
    }

    private static func le32(_ data: Data, _ at: Int) -> UInt32 {
        UInt32(le16(data, at)) | UInt32(le16(data, at + 2)) << 16
    }

    private static func lastIndex(of pattern: [UInt8], in data: Data) -> Int? {
        let bytes = [UInt8](data)
        guard bytes.count >= pattern.count + 18 else { return nil }
        var index = bytes.count - 22
        while index >= 0 {
            if Array(bytes[index..<index + pattern.count]) == pattern { return index }
            index -= 1
        }
        return nil
    }
}

/// The app's web view controller: Capacitor's, plus the app's own plugin.
class FlipparrViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(FlipparrNativePlugin())
        // iOS's rubber band, which Capacitor turns off: pull to refresh rides
        // it (src/frame/pullRefresh.jsx), as on the web from a Home Screen,
        // and a page that stops dead at its ends is not how iOS scrolls (the
        // owner, 2026-10-11). Always, so a short page can be pulled too.
        webView?.scrollView.bounces = true
        webView?.scrollView.alwaysBounceVertical = true
    }
}
