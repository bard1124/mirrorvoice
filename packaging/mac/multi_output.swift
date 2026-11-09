// Create (idempotently) a Multi-Output device "Teams 扬声器 (耳机+字幕)" = <listen device> + BlackHole 16ch,
// so Teams' speaker audio reaches your ears AND the subtitle listener.
// Usage: multi_output [listen-device-name-substring]            create if missing (default: 外置耳机)
//        multi_output --members                                  print the sub-devices, one per line
//        multi_output --rebuild <listen-device-name-substring>   replace it (new headphones)
//        multi_output --destroy                                  remove it
import CoreAudio
import Foundation

let env = ProcessInfo.processInfo.environment
let multiName = env["MIRRORVOICE_MULTI_NAME"] ?? "Teams 扬声器 (耳机+字幕)"   // override for tests only
let multiUID = env["MIRRORVOICE_MULTI_UID"] ?? "local.zh2en-voice.multi-output"
let args = Array(CommandLine.arguments.dropFirst())
let mode = args.first.map { $0.hasPrefix("--") ? $0 : "" } ?? ""
let listenName = (mode.isEmpty ? args.first : args.dropFirst().first) ?? "外置耳机"

func allDevices() -> [AudioDeviceID] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size)
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids)
    return ids
}

func stringProp(_ id: AudioDeviceID, _ sel: AudioObjectPropertySelector) -> String {
    var addr = AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &value) == noErr, let v = value else { return "" }
    return v.takeRetainedValue() as String
}

func hasOutput(_ id: AudioDeviceID) -> Bool {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams,
                                          mScope: kAudioObjectPropertyScopeOutput,
                                          mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size)
    return size > 0
}

let devices = allDevices().map { (id: $0, name: stringProp($0, kAudioObjectPropertyName),
                                  uid: stringProp($0, kAudioDevicePropertyDeviceUID)) }

func subDeviceUIDs(_ id: AudioDeviceID) -> [String] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioAggregateDevicePropertyFullSubDeviceList,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var value: Unmanaged<CFArray>?
    var size = UInt32(MemoryLayout<Unmanaged<CFArray>?>.size)
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &value) == noErr, let v = value else { return [] }
    return (v.takeRetainedValue() as? [String]) ?? []
}

let existing = devices.first(where: { $0.uid == multiUID })
if mode == "--members" {
    guard let multi = existing else { print("none"); exit(1) }
    for uid in subDeviceUIDs(multi.id) {
        print(devices.first(where: { $0.uid == uid })?.name ?? "missing:\(uid)")
    }
    exit(0)
}
if mode == "--destroy" {
    guard let multi = existing else { print("none"); exit(0) }
    let derr = AudioHardwareDestroyAggregateDevice(multi.id)
    print(derr == noErr ? "destroyed: \(multiName)" : "error: destroy failed (\(derr))")
    exit(derr == noErr ? 0 : 5)
}
if let multi = existing {
    if mode != "--rebuild" {
        print("exists: \(multiName)")
        exit(0)
    }
    let derr = AudioHardwareDestroyAggregateDevice(multi.id)
    if derr != noErr { print("error: AudioHardwareDestroyAggregateDevice failed (\(derr))"); exit(5) }
}
guard let listen = devices.first(where: { $0.name.contains(listenName) && hasOutput($0.id) }) else {
    print("error: output device containing '\(listenName)' not found"); exit(2)
}
guard let bh = devices.first(where: { $0.name.contains("BlackHole 16ch") }) else {
    print("error: BlackHole 16ch not found (brew install --cask blackhole-16ch, then sudo killall coreaudiod)"); exit(3)
}

let desc: [String: Any] = [
    kAudioAggregateDeviceNameKey: multiName,
    kAudioAggregateDeviceUIDKey: multiUID,
    kAudioAggregateDeviceIsPrivateKey: 0,
    kAudioAggregateDeviceIsStackedKey: 1,  // stacked = Multi-Output (same audio to every sub-device)
    kAudioAggregateDeviceMainSubDeviceKey: listen.uid,
    kAudioAggregateDeviceSubDeviceListKey: [
        [kAudioSubDeviceUIDKey: listen.uid],
        [kAudioSubDeviceUIDKey: bh.uid, kAudioSubDeviceDriftCompensationKey: 1],
    ],
]
var newID: AudioDeviceID = 0
let err = AudioHardwareCreateAggregateDevice(desc as CFDictionary, &newID)
if err != noErr { print("error: AudioHardwareCreateAggregateDevice failed (\(err))"); exit(4) }
print("created: \(multiName) = \(listen.name) + \(bh.name)")
