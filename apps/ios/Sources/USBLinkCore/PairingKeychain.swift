import CryptoKit
import Foundation
import Security

public enum PairingKeychain {
    public enum Failure: Error {
        case invalidSecret
        case keychain(OSStatus)
        case random(OSStatus)
    }

    private static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "org.usblinklab.ios.pairing",
        kSecAttrAccount as String: "configured-peer-v1",
        kSecAttrSynchronizable as String: false
    ]

    // SHA-256 of the disclosed interoperability PSK. The live app never embeds that PSK.
    private static let fixtureDigest = "630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd"

    public static func generate() throws -> Data {
        var secret = Data(count: 32)
        let count = secret.count
        let result = secret.withUnsafeMutableBytes { (bytes: UnsafeMutableRawBufferPointer) in
            SecRandomCopyBytes(kSecRandomDefault, count, bytes.baseAddress!)
        }
        guard result == errSecSuccess else { throw Failure.random(result) }
        return secret
    }

    public static func load() throws -> Data? {
        var search = query
        search[kSecReturnData as String] = true
        search[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let status = SecItemCopyMatching(search as CFDictionary, &value)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw Failure.keychain(status) }
        guard let secret = value as? Data else { throw Failure.invalidSecret }
        try validate(secret)
        return secret
    }

    public static func save(_ secret: Data?) throws {
        guard let secret else {
            let status = SecItemDelete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw Failure.keychain(status) }
            return
        }
        try validate(secret)
        let attributes: [String: Any] = [
            kSecValueData as String: secret,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        ]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let addStatus = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
            guard addStatus == errSecSuccess else { throw Failure.keychain(addStatus) }
        } else if status != errSecSuccess {
            throw Failure.keychain(status)
        }
    }

    private static func validate(_ secret: Data) throws {
        guard secret.count == 32, Set(secret).count > 1 else { throw Failure.invalidSecret }
        let digest = SHA256.hash(data: secret).map { String(format: "%02x", $0) }.joined()
        guard digest != fixtureDigest else { throw Failure.invalidSecret }
    }
}
