// Local OCR bridge. Outputs JSON only; no video or image leaves this machine.
import Foundation
import Vision

var output: [[String: Any]] = []
for path in CommandLine.arguments.dropFirst() {
    autoreleasepool {
        do {
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
            request.usesLanguageCorrection = false
            let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:])
            try handler.perform([request])
            let lines: [[String: Any]] = (request.results ?? []).compactMap { observation in
                guard let text = observation.topCandidates(1).first else { return nil }
                return ["text": text.string, "confidence": Double(text.confidence)]
            }
            output.append(["file": path, "lines": lines])
        } catch {
            output.append(["file": path, "lines": [], "error": "vision_recognition_failed"])
        }
    }
}
let data = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
FileHandle.standardOutput.write(data)
