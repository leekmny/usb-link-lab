import Foundation

enum LabLanguage: String, CaseIterable, Identifiable {
    case ko, en

    var id: String { rawValue }
    var locale: Locale { Locale(identifier: self == .ko ? "ko_KR" : "en_US") }
    var name: String { self == .ko ? "한국어" : "English" }

    func text(_ key: LabCopy) -> String { self == .ko ? key.words.0 : key.words.1 }

    func format(_ key: LabCopy, _ arguments: CVarArg...) -> String {
        String(format: text(key), locale: locale, arguments: arguments)
    }

    func bytes(_ value: UInt64) -> String {
        ByteCountFormatStyle(style: .file, spellsOutZero: false, locale: locale)
            .format(Int64(clamping: value))
    }

    func percent(_ value: Double) -> String {
        value.formatted(.percent.precision(.fractionLength(0)).locale(locale))
    }
}

enum LabCopy {
    case workbench, history, settings
    case synthetic, connection, unpaired, starting, waiting, linked, reconnecting, reconnectInstruction, paused, unavailable
    case setupLink, firstPairingInstruction, hostInstruction, linkedInstruction, pausedInstruction, resumeQueue, port, offlineTitle
    case compose, draftHint, draftPlaceholder, textLimit, protection, photo, file, addToQueue
    case plaintextExposure, authenticatedExposure, encryptedExposure, confidentiality, authenticity, yes, no
    case attachmentConfirm, selectedPhoto, selectedFile, exactBytes, sourceUnavailable, sourceTooLarge, sourcePhotoFormat
    case sourceReading, dismiss, back, done, attachmentReady, hideKeyboard
    case activity, allHistory, noActivity, noRecords, direction, incoming, outgoing, message, image, genericFile
    case queued, sending, receiving, delivered, checked, interrupted, unconfirmed
    case details, transferred, total, progressBytes, status, fileName, mediaType, origin, summary, acknowledgePending, completeLocal, completePlaintext, completeRemote
    case stopSession, stopDescription, keepGoing, remove, retry, retryTitle, retryDescription, duplicateWarning, retryQueued
    case deleteMessage, confirmDeleteMessage, deleteMessageDescription, cancel
    case selectMessages, selectedMessageCount, deleteSelectedMessages, confirmDeleteSelectedMessages, deleteSelectedMessagesDescription
    case pairing, pairingExplanation, scanWindowsQR, enterPairingCode, showWindowsQR, invalidPairingQR
    case cameraDenied, cameraUnavailable, enterWindowsCode, pairingCodeField, invalidPairingCode
    case preparePairing, preparingPairing, qrRecognized, pairingReady, startWindowsPairing, pairingTimeRemaining
    case pairingConnecting, pairingExpired, pairingExpiredHelp, pairingCompleted, pairingCompletedHelp
    case pairingAttemptFailed, pairingTryAgain, cancelPairing, forgetPairing, forgetDescription, forgetPairingFailed
    case language, appearance, system, light, dark, diagnostics, revision, localStorage, testOnly, testMode
    case connectionScope, listenerFailed, protocolFailed, timeout, disconnected, background, storage, operationFailed
    case queueFailed, queueFull, mustPair, resumeFailed, exportFailed, queueBusy, pairedState, queuePaused, queueRunning, handshake
    case preview, previewUnavailable, previewLabel, saveFiles, share, saved, savedFiles, retryNotice, detailsUnknown, pairingMismatch, draining

    var words: (String, String) {
        switch self {
        case .workbench: ("전송", "Transfer")
        case .history: ("기록", "History")
        case .settings: ("설정", "Settings")
        case .synthetic: ("시뮬레이터 예시 · USB 사용 안 함", "Simulator example · No USB")
        case .connection: ("연결", "Connection")
        case .unpaired: ("아직 페어링되지 않음", "Pairing required")
        case .starting: ("수신 준비 중", "Starting listener")
        case .waiting: ("Windows 연결 대기", "Waiting for Windows")
        case .linked: ("연결됨", "Connected")
        case .reconnecting: ("다시 연결 대기", "Waiting to reconnect")
        case .reconnectInstruction: ("USB 케이블을 연결한 채 두 앱을 열어 두면 Windows에서 다시 연결을 시도합니다.",
                                     "Keep both apps open and the USB cable connected so Windows can try reconnecting.")
        case .paused: ("대기열 일시 정지", "Queue paused")
        case .unavailable: ("연결 문제", "Connection issue")
        case .setupLink: ("페어링 설정", "Set up pairing")
        case .firstPairingInstruction: ("Windows 앱에서 페어링 초대를 만든 다음 이 iPhone에서 준비하세요.", "Create a pairing invitation in the Windows app, then prepare it on this iPhone.")
        case .hostInstruction: ("iPhone을 잠금 해제한 상태로 이 앱을 열어 두세요. Windows에서 USB 기기를 선택하고 연결하세요.", "Keep this app open on your unlocked iPhone. Select the USB device and connect from Windows.")
        case .linkedInstruction: ("Windows 앱과 인증된 세션이 열려 있습니다.", "An authenticated session with the Windows app is open.")
        case .pausedInstruction: ("새 세션에서 대기 중인 항목을 보내려면 대기열을 재개하세요.", "Resume the queue to send waiting items in a new session.")
        case .resumeQueue: ("대기열 재개", "Resume queue")
        case .port: ("리스너", "Listener")
        case .offlineTitle: ("Windows 앱과의 연결이 필요합니다.", "A connection from the Windows app is required.")
        case .compose: ("보낼 항목 만들기", "Create a transfer")
        case .draftHint: ("대기열에 추가하면 이 항목의 보호 방식과 내용이 고정됩니다.", "Adding an item to the queue freezes its protection and content.")
        case .draftPlaceholder: ("메시지 입력", "Write a message")
        case .textLimit: ("메시지는 최대 1 MiB(1,048,576바이트)입니다.", "A message can contain up to 1 MiB (1,048,576 bytes).")
        case .protection: ("항목 보호 방식", "Item protection")
        case .photo: ("사진", "Photo")
        case .file: ("파일", "File")
        case .addToQueue: ("대기열에 추가", "Add to queue")
        case .plaintextExposure: ("내용과 파일 이름을 누구나 읽고 변경할 수 있습니다.", "Anyone observing the stream can read and modify the content and filename.")
        case .authenticatedExposure: ("암호화 없이 내용을 인증합니다. 내용과 파일 이름은 공개됩니다.", "Authenticates without encryption. Content and filename are visible.")
        case .encryptedExposure: ("내용과 파일 이름을 암호화하고 변경 여부를 인증으로 확인합니다.", "Encrypts content and filename and authenticates them against changes.")
        case .confidentiality: ("기밀성", "Confidentiality")
        case .authenticity: ("내용 인증", "Content authentication")
        case .yes: ("있음", "Yes")
        case .no: ("없음", "No")
        case .attachmentConfirm: ("첨부 확인", "Confirm attachment")
        case .selectedPhoto: ("사진 앱에서 제공한 현재 표현의 파일을 그대로 전송합니다. 사진 앱이 제공하는 원본과 다를 수 있습니다.", "Sends the current file representation supplied by Photos. It may differ from the original asset.")
        case .selectedFile: ("파일에서 읽은 바이트를 그대로 전송합니다.", "Sends the bytes read from the selected file.")
        case .exactBytes: ("%@ · 정확히 %@바이트", "%@ · %@ bytes exactly")
        case .sourceUnavailable: ("선택한 파일을 읽을 수 없습니다. iCloud 항목은 기기에 내려받은 뒤 다시 선택하세요.", "The selected file could not be read. For an iCloud item, download it to this device and select it again.")
        case .sourceTooLarge: ("첨부는 최대 1 GiB입니다. 더 작은 파일을 선택하세요.", "Attachments are limited to 1 GiB. Choose a smaller file.")
        case .sourcePhotoFormat: ("사진의 파일 형식을 확인할 수 없습니다. 사진을 파일에 저장해 파일로 선택하세요.", "The photo’s file format could not be identified. Save it to Files and select it there.")
        case .sourceReading: ("선택한 파일을 준비하는 중…", "Preparing selected file…")
        case .dismiss: ("닫기", "Close")
        case .back: ("취소", "Cancel")
        case .done: ("완료", "Done")
        case .attachmentReady: ("첨부 준비됨", "Attachment ready")
        case .hideKeyboard: ("키보드 닫기", "Dismiss keyboard")
        case .activity: ("대기열 및 전송", "Queue and activity")
        case .allHistory: ("전체 기록", "All history")
        case .noActivity: ("대기 중이거나 전송 중인 항목이 없습니다.", "No items are queued or transferring.")
        case .noRecords: ("항목을 대기열에 추가하거나 Windows에서 보내면 여기에 기록됩니다.", "Items appear here after you queue them or receive them from Windows.")
        case .direction: ("방향", "Direction")
        case .incoming: ("받기", "Incoming")
        case .outgoing: ("보내기", "Outgoing")
        case .message: ("메시지", "Message")
        case .image: ("이미지", "Image")
        case .genericFile: ("파일", "File")
        case .queued: ("대기 중", "Queued")
        case .sending: ("전송 중", "Sending")
        case .receiving: ("수신 중", "Receiving")
        case .delivered: ("전달 완료", "Delivered")
        case .checked: ("수신 및 확인 완료", "Received and checked")
        case .interrupted: ("중단됨", "Interrupted")
        case .unconfirmed: ("보낸 결과 미확인", "Delivery unconfirmed")
        case .details: ("항목 세부 정보", "Transfer details")
        case .transferred: ("전송한 바이트", "Transferred")
        case .total: ("전체 바이트", "Total")
        case .progressBytes: ("%@ / %@", "%@ / %@")
        case .status: ("상태", "Status")
        case .fileName: ("파일 이름", "Filename")
        case .mediaType: ("미디어 유형", "Media type")
        case .origin: ("출처", "Source")
        case .summary: ("전송 정보", "Transfer information")
        case .acknowledgePending: ("상대 앱의 최종 확인을 기다립니다.", "Waiting for final confirmation from the peer.")
        case .completePlaintext: ("해시와 바이트 수가 일치해 앱에 저장했습니다. 내용과 출처는 인증되지 않습니다.", "The hash and byte count matched, and the item was saved in the app. Its content and origin are unauthenticated.")
        case .completeLocal: ("검사를 마친 항목이 앱에 저장되었습니다.", "The checked item has been saved in the app.")
        case .completeRemote: ("상대 앱이 저장 완료를 확인했습니다.", "The peer confirmed it saved the item.")
        case .stopSession: ("세션 중단", "Stop session")
        case .stopDescription: ("현재 세션의 보내기와 받기를 모두 중단합니다. 대기 중인 항목은 보관되며 직접 대기열을 재개할 수 있습니다.", "Stops active sends and receives in this session. Waiting items are kept until you resume the queue.")
        case .keepGoing: ("계속 전송", "Keep transferring")
        case .remove: ("대기열에서 제거", "Remove from queue")
        case .deleteMessage: ("메시지 삭제", "Delete message")
        case .confirmDeleteMessage: ("메시지를 삭제할까요?", "Delete this message?")
        case .deleteMessageDescription: ("이 기기의 기록과 저장된 본문이 삭제됩니다.", "This device will remove the record and its saved message.")
        case .selectMessages: ("선택", "Select")
        case .selectedMessageCount: ("%d개 선택", "%d selected")
        case .deleteSelectedMessages: ("선택한 메시지 삭제", "Delete selected messages")
        case .confirmDeleteSelectedMessages: ("선택한 메시지를 삭제할까요?", "Delete selected messages?")
        case .deleteSelectedMessagesDescription: ("이 기기에서 선택한 메시지의 기록과 저장된 본문이 삭제됩니다.", "This device will remove the selected records and their saved messages.")
        case .cancel: ("취소", "Cancel")
        case .retry: ("다시 보내기", "Retry")
        case .retryTitle: ("%@으로 다시 보내기", "Retry with %@")
        case .retryDescription: ("보호 방식과 내용을 유지해 새로 인증한 세션에서 처음부터 보냅니다.", "Sends from the beginning in a fresh authenticated session with the same protection and content.")
        case .duplicateWarning: ("상대가 이미 저장했을 수 있습니다. 다시 보내면 중복 항목이 생길 수 있습니다.", "The peer may have saved this item. Retrying can create a duplicate.")
        case .retryQueued: ("다시 보낼 항목을 대기열에 추가했습니다.", "The item was queued to send again.")
        case .pairing: ("앱 페어링", "App pairing")
        case .pairingExplanation: ("USB로 연결한 Windows PC에서 USBLinkLab의 페어링 화면을 열어 주세요.", "Connect your iPhone by USB and open the USBLinkLab pairing screen on your Windows PC.")
        case .scanWindowsQR: ("Windows QR 스캔", "Scan Windows QR")
        case .enterPairingCode: ("6자리 코드 입력", "Enter six-digit code")
        case .showWindowsQR: ("Windows 앱에 표시된 QR 코드를 카메라에 비춰 주세요.", "Point the camera at the QR code displayed in the Windows app.")
        case .invalidPairingQR: ("이 QR 코드를 사용할 수 없습니다. Windows 앱에서 새 QR 코드를 만들어 스캔하세요.", "This QR code cannot be used. Generate a new QR code in the Windows app and scan it.")
        case .cameraDenied: ("카메라 접근이 허용되지 않았습니다. Windows 앱에 표시된 6자리 코드를 입력하세요.", "Camera access is off. Enter the six-digit code shown in the Windows app.")
        case .cameraUnavailable: ("카메라를 사용할 수 없습니다. Windows 앱에 표시된 6자리 코드를 입력하세요.", "The camera is unavailable. Enter the six-digit code shown in the Windows app.")
        case .enterWindowsCode: ("Windows 앱에 표시된 6자리 코드를 첫 자리의 0도 빼지 말고 그대로 입력하세요.", "Enter the six digits from the Windows app, including any leading zeros.")
        case .pairingCodeField: ("6자리 코드", "Six-digit code")
        case .invalidPairingCode: ("숫자 0–9로 정확히 6자리를 입력하세요.", "Enter exactly six digits from 0 to 9.")
        case .preparePairing: ("준비", "Prepare")
        case .preparingPairing: ("페어링 준비 중", "Preparing pairing")
        case .qrRecognized: ("QR 코드를 읽었습니다", "QR code scanned")
        case .pairingReady: ("페어링 준비 완료", "Ready to pair")
        case .startWindowsPairing: ("이 화면을 열어 둔 채 Windows 앱에서 ‘페어링 시작’을 선택하세요.", "Keep this screen open and choose Start pairing in the Windows app.")
        case .pairingTimeRemaining: ("%d초 남음", "%d seconds remaining")
        case .pairingConnecting: ("Windows와 페어링 중", "Pairing with Windows")
        case .pairingExpired: ("초대가 만료되었습니다", "Invitation expired")
        case .pairingExpiredHelp: ("Windows 앱에서 새 초대를 만든 뒤 다시 준비하세요.", "Generate a new invitation in the Windows app and prepare again.")
        case .pairingCompleted: ("페어링 완료", "Pairing complete")
        case .pairingCompletedHelp: ("이제 이 Windows 앱과 USB로 다시 연결할 수 있습니다.", "You can now reconnect to this Windows app over USB.")
        case .pairingAttemptFailed: ("페어링에 실패했습니다", "Pairing failed")
        case .pairingTryAgain: ("USB 연결을 확인하고 Windows 앱에서 새 초대를 만들어 다시 시도하세요.", "Check the USB connection and generate a new invitation in the Windows app before trying again.")
        case .cancelPairing: ("페어링 취소", "Cancel pairing")
        case .forgetPairing: ("페어링 키 삭제", "Forget pairing key")
        case .forgetDescription: ("현재 세션이 닫힙니다. 새 페어링 후 이전 키에 연결된 항목을 보내려면 대기열에 다시 추가하세요.", "The current session closes. After pairing again, add any items bound to the old key to the queue again.")
        case .forgetPairingFailed: ("페어링 키를 삭제할 수 없습니다. iPhone을 잠금 해제하고 다시 시도하세요.", "The pairing key could not be removed. Unlock your iPhone and try again.")
        case .language: ("언어", "Language")
        case .appearance: ("테마", "Appearance")
        case .system: ("시스템 설정", "System")
        case .light: ("라이트", "Light")
        case .dark: ("다크", "Dark")
        case .diagnostics: ("연결 진단", "Connection diagnostics")
        case .revision: ("프로토콜", "Protocol")
        case .localStorage: ("항목 보호 방식은 전송 중에 적용됩니다. 기기에 저장된 기록, 내보낸 파일, 스크린샷은 별도로 관리하세요.", "The selected protection applies in transit. Manage local history, exported files, and screenshots separately.")
        case .testOnly: ("시뮬레이터 예시에서는 실제 전송을 실행하지 않습니다.", "The simulator example does not perform real transfers.")
        case .testMode: ("합성 예시", "Synthetic example")
        case .connectionScope: ("iPhone에는 로컬 소켓이 표시됩니다. 실제 USB 연결 여부는 Windows 앱에서 선택한 기기와 케이블 테스트로 확인하세요.", "The iPhone sees a local socket. Confirm physical USB with the selected device in the Windows app and a cable test.")
        case .listenerFailed: ("USB 수신 대기를 시작할 수 없습니다. 앱을 열어 두고 다시 시도할 때까지 기다려 주세요.",
                               "The USB listener could not start. Keep this app open while it retries.")
        case .protocolFailed: ("세션 검사가 실패했습니다. 두 앱의 프로토콜 버전과 페어링 키를 확인하세요.", "The session check failed. Verify the protocol version and pairing key in both apps.")
        case .timeout: ("시간 초과로 연결이 끊겼습니다. USB 케이블을 연결하고 두 앱을 열어 두면 Windows에서 재연결을 시도합니다.",
                          "The session timed out. Keep both apps open with USB connected so Windows can try reconnecting.")
        case .disconnected: ("연결이 종료되었습니다. Windows에서 기기와 케이블을 확인하고 다시 연결하세요.", "The connection closed. Check the device and cable in Windows, then reconnect.")
        case .background: ("앱이 비활성화되어 세션이 종료되었습니다. 이 앱을 다시 열면 Windows에서 재연결을 시도할 수 있습니다.",
                             "The app became inactive and the session closed. Reopen it so Windows can try reconnecting.")
        case .storage: ("기기에 파일을 보관할 수 없습니다. 저장 공간과 파일 접근 가능 여부를 확인하세요.", "The item could not be stored on this device. Check available space and access to the source file.")
        case .operationFailed: ("요청을 처리할 수 없습니다. 항목 상태를 확인하고 다시 시도하세요.", "The action failed. Check the item status and try again.")
        case .queueFailed: ("대기열에 추가할 수 없습니다. 페어링과 대기열, 기기 저장 공간을 확인하세요.", "The item could not be queued. Check pairing, queue capacity, and device storage.")
        case .queueFull: ("대기열이 가득 찼습니다. 전송을 기다리거나 대기 중인 항목을 제거하세요.", "The queue is full. Wait for a transfer or remove a queued item.")
        case .mustPair: ("항목을 보내려면 두 앱에 동일한 페어링 키를 설정하세요.", "Set the same pairing key in both apps before sending an item.")
        case .resumeFailed: ("대기열을 재개하려면 두 앱에 동일한 페어링 키를 설정하세요.", "Set the same pairing key in both apps to resume the queue.")
        case .exportFailed: ("파일을 준비할 수 없습니다. 저장 공간과 파일이 앱에 남아 있는지 확인하세요.", "The file could not be prepared. Check storage space and whether the file is still in the app.")
        case .queueBusy: ("항목 추가 중…", "Adding item…")
        case .pairedState: ("키 보관됨", "Key stored")
        case .queuePaused: ("일시 정지됨", "Paused")
        case .queueRunning: ("작동 중", "Active")
        case .handshake: ("연결 인증 중", "Authenticating connection")
        case .preview: ("미리보기", "Preview")
        case .previewUnavailable: ("이 이미지의 미리보기를 만들 수 없습니다.", "A preview could not be made for this image.")
        case .previewLabel: ("수신한 이미지 미리보기", "Preview of received image")
        case .saveFiles: ("파일에 저장", "Save to Files")
        case .share: ("공유", "Share")
        case .saved: ("저장됨", "Saved")
        case .savedFiles: ("파일에 저장했습니다.", "Saved to Files.")
        case .retryNotice: ("이 항목은 새 연결에서 다시 시작합니다.", "This item starts again on a new connection.")
        case .pairingMismatch: ("이 항목은 이전 페어링 키에 연결되어 있습니다. 이전 키를 다시 설정하거나 대기열에서 제거한 뒤 다시 추가하세요.", "This item is bound to an earlier pairing key. Restore that key, or remove the item and add it again.")
        case .draining: ("현재 세션의 전송을 마친 뒤 Windows에서 새 세션을 엽니다.", "Windows will open a fresh session after active transfers finish.")
        case .detailsUnknown: ("아직 확인되지 않음", "Not known yet")
        }
    }
}
