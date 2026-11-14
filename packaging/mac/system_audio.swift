// The sound the Mac is playing — the meeting, a stream — as mono float32 on stdout, for subtitles.
// A Core Audio process tap (macOS 14.4+): no virtual device, no multi-output to set up in the meeting
// app; it needs the "System Audio Recording" privacy grant of the app that starts it (its Info.plist
// carries NSAudioCaptureUsageDescription). Refused, the tap still runs and delivers silence.
//
// Usage: system_audio [pid]     pid: a process whose own sound is left out (the app: its monitor)
// stderr, first line: "format <sample rate> <channels>"; then stdout: raw float32, mono, that rate.
// Exits when the reader goes away, when the parent dies, and when the output device changes (a
// Bluetooth headset drops to 16 or 24 kHz when a meeting opens its microphone): start it again.
// The approach follows Cicada's SystemAudioTap (github.com/wquguru/cicada, Apache-2.0).
import AudioToolbox
import CoreAudio
import Foundation

func say(_ msg: String) { FileHandle.standardError.write((msg + "\n").data(using: .utf8)!) }
func fail(_ msg: String, _ code: Int32) -> Never { say("error: " + msg); exit(code) }

func address(_ sel: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
}

guard #available(macOS 14.4, *) else { fail("needs macOS 14.4", 2) }

let system = AudioObjectID(kAudioObjectSystemObject)
var outputID = AudioDeviceID(0)
var size = UInt32(MemoryLayout<AudioDeviceID>.size)
var addr = address(kAudioHardwarePropertyDefaultOutputDevice)
guard AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &outputID) == noErr, outputID != 0 else { fail("no output device", 3) }
var uidRef: Unmanaged<CFString>?
size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
addr = address(kAudioDevicePropertyDeviceUID)
guard AudioObjectGetPropertyData(outputID, &addr, 0, nil, &size, &uidRef) == noErr, let uid = uidRef else { fail("no output uid", 3) }
let outputUID = uid.takeRetainedValue() as String

var leftOut: [AudioObjectID] = []
if CommandLine.arguments.count > 1, var pid = pid_t(CommandLine.arguments[1]), pid > 0 {
    var object = AudioObjectID(0)
    size = UInt32(MemoryLayout<AudioObjectID>.size)
    addr = address(kAudioHardwarePropertyTranslatePIDToProcessObject)
    if AudioObjectGetPropertyData(system, &addr, UInt32(MemoryLayout<pid_t>.size), &pid, &size, &object) == noErr, object != 0 {
        leftOut.append(object)
    }
}

let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: leftOut)
tapDescription.name = "MirrorVoice"
tapDescription.isPrivate = true
tapDescription.muteBehavior = .unmuted
var tapID = AudioObjectID(0)
var status = AudioHardwareCreateProcessTap(tapDescription, &tapID)
guard status == noErr else { fail("tap \(status)", 4) }

var format = AudioStreamBasicDescription()
size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
addr = address(kAudioTapPropertyFormat)
guard AudioObjectGetPropertyData(tapID, &addr, 0, nil, &size, &format) == noErr else { fail("tap format", 5) }

let aggregate: [String: Any] = [
    kAudioAggregateDeviceNameKey: "MirrorVoice Tap",
    kAudioAggregateDeviceUIDKey: "io.github.mirrorvoice.tap.\(getpid())",
    kAudioAggregateDeviceMainSubDeviceKey: outputUID,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
    kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: tapDescription.uuid.uuidString,
                                       kAudioSubTapDriftCompensationKey: true]],
]
var aggregateID = AudioObjectID(0)
status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
guard status == noErr else { AudioHardwareDestroyProcessTap(tapID); fail("aggregate \(status)", 6) }

func leave(_ code: Int32) -> Never {
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
    exit(code)
}

say("format \(Int(format.mSampleRate)) \(Int(format.mChannelsPerFrame))")

// The audio thread copies and hands on; a writer the reader has stopped serving is not fed without end.
let out = FileHandle.standardOutput
let writer = DispatchQueue(label: "writer")
let pendingLock = NSLock()
var pending = 0
let pendingLimit = Int(format.mSampleRate) * MemoryLayout<Float32>.size * 2  // two seconds
var ioProc: AudioDeviceIOProcID?
status = AudioDeviceCreateIOProcIDWithBlock(&ioProc, aggregateID, nil) { _, input, _, _, _ in
    let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
    guard let first = list.first, let data = first.mData else { return }
    let channels = max(1, Int(first.mNumberChannels))
    let frames = Int(first.mDataByteSize) / MemoryLayout<Float32>.size / channels
    let samples = data.bindMemory(to: Float32.self, capacity: frames * channels)
    var mono = [Float32](repeating: 0, count: frames)
    for i in 0..<frames {
        var s: Float32 = 0
        for c in 0..<channels { s += samples[i * channels + c] }
        mono[i] = s / Float32(channels)
    }
    let bytes = mono.withUnsafeBufferPointer { Data(buffer: $0) }
    pendingLock.lock()
    let room = pending + bytes.count <= pendingLimit
    if room { pending += bytes.count }
    pendingLock.unlock()
    guard room else { return }
    writer.async {
        out.write(bytes)
        pendingLock.lock(); pending -= bytes.count; pendingLock.unlock()
    }
}
guard status == noErr, let proc = ioProc else { fail("ioproc \(status)", 7) }
status = AudioDeviceStart(aggregateID, proc)
guard status == noErr else { leave(8) }

// Another output device (headphones plugged in, a headset's rate changed): the tap is on the old one.
var outputAddress = address(kAudioHardwarePropertyDefaultOutputDevice)
AudioObjectAddPropertyListenerBlock(system, &outputAddress, DispatchQueue.main) { _, _ in say("output changed"); leave(10) }
var rateAddress = address(kAudioDevicePropertyNominalSampleRate)
AudioObjectAddPropertyListenerBlock(outputID, &rateAddress, DispatchQueue.main) { _, _ in say("rate changed"); leave(10) }

signal(SIGPIPE, SIG_IGN)
for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { leave(0) }
    source.resume()
    objc_setAssociatedObject(out, "\(sig)", source, .OBJC_ASSOCIATION_RETAIN)  // keep it alive
}
let parent = getppid()
Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { _ in if getppid() != parent { leave(0) } }
RunLoop.main.run()
