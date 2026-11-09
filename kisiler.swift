// Mac "Kişiler" uygulamasındaki kişileri JSON olarak stdout'a yazar.
// Derleme: swiftc -O kisiler.swift -o kisiler
import Contacts
import Foundation

let store = CNContactStore()
let sem = DispatchSemaphore(value: 0)
var granted = false
store.requestAccess(for: .contacts) { ok, err in
    granted = ok
    if let err = err { FileHandle.standardError.write("Hata: \(err.localizedDescription)\n".data(using: .utf8)!) }
    sem.signal()
}
sem.wait()
guard granted else {
    FileHandle.standardError.write("Kişiler izni yok. Sistem Ayarları → Gizlilik ve Güvenlik → Kişiler'den Terminal'e izin ver.\n".data(using: .utf8)!)
    exit(2)
}

let keys: [CNKeyDescriptor] = [
    CNContactIdentifierKey, CNContactGivenNameKey, CNContactFamilyNameKey,
    CNContactMiddleNameKey, CNContactOrganizationNameKey,
    CNContactPhoneNumbersKey, CNContactEmailAddressesKey,
].map { $0 as CNKeyDescriptor }

var out: [[String: Any]] = []
let req = CNContactFetchRequest(keysToFetch: keys)
req.unifyResults = true
do {
    try store.enumerateContacts(with: req) { c, _ in
        out.append([
            "id": c.identifier,
            "given": c.givenName,
            "family": c.familyName,
            "middle": c.middleName,
            "org": c.organizationName,
            "phones": c.phoneNumbers.map { ["label": $0.label ?? "", "number": $0.value.stringValue] },
            "emails": c.emailAddresses.map { ["label": $0.label ?? "", "address": $0.value as String] },
        ])
    }
} catch {
    FileHandle.standardError.write("Okuma hatası: \(error.localizedDescription)\n".data(using: .utf8)!)
    exit(1)
}
let data = try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
FileHandle.standardOutput.write(data)
