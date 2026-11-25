import XCTest

final class USBLinkLabUITests: XCTestCase {
    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    func testKoreanOnboardingAndFourProtectionChoices() {
        let app = start("onboarding")
        XCTAssertTrue(app.staticTexts["synthetic-label"].waitForExistence(timeout: 10))
        let status = app.descendants(matching: .any)["connection-status"].firstMatch
        XCTAssertEqual(status.label, "아직 페어링되지 않음")
        capture("01-korean-onboarding-light", app)

        let selector = app.buttons["draft-protection"]
        XCTAssertTrue(selector.exists)
        XCTAssertEqual(selector.value as? String, "AES-256-GCM")
        if !selector.isHittable { app.scrollViews.firstMatch.swipeUp() }
        selector.tap()
        XCTAssertTrue(app.buttons["Plaintext"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["AES-256-GCM"].exists)
        XCTAssertTrue(app.buttons["ChaCha20-Poly1305"].exists)
        XCTAssertTrue(app.buttons["HMAC-SHA256"].exists)
        app.buttons["Plaintext"].tap()
        XCTAssertEqual(selector.value as? String, "Plaintext")
        XCTAssertTrue(app.staticTexts["draft-exposure"].label.contains("누구나"))
    }

    func testSyntheticConnectedProgressAndFailureCapture() {
        let progress = start("transfer", dark: true)
        let status = progress.descendants(matching: .any)["connection-status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 10))
        XCTAssertEqual(status.label, "연결됨")
        capture("02-connected-dark", progress)

        progress.tabBars.buttons["기록"].tap()
        XCTAssertTrue(progress.buttons["item-sending"].waitForExistence(timeout: 10))
        capture("03-active-transfer-dark", progress)
        progress.terminate()

        let failure = start("failure", accessible: true)
        failure.tabBars.buttons["기록"].tap()
        let row = failure.buttons["item-unconfirmed"]
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        XCTAssertTrue(failure.navigationBars["항목 세부 정보"].waitForExistence(timeout: 5))
        capture("04-unconfirmed-accessibility", failure)
    }

    func testCameraUnavailableFallsBackToSixDigitPairing() {
        let app = start("onboarding")
        app.buttons["open-pairing"].tap()
        let scan = app.buttons["scan-pairing-qr"]
        XCTAssertTrue(scan.waitForExistence(timeout: 5))
        scan.tap()
        XCTAssertTrue(app.staticTexts["pairing-camera-feedback"].waitForExistence(timeout: 5))

        let useCode = app.buttons["enter-pairing-code"]
        useCode.tap()
        let code = app.secureTextFields["pairing-code"]
        XCTAssertTrue(code.waitForExistence(timeout: 5))
        let prepare = app.buttons["prepare-pairing"]
        XCTAssertFalse(prepare.isEnabled)
        code.tap()
        code.typeText("00123")
        XCTAssertFalse(prepare.isEnabled)
        code.typeText("4")
        XCTAssertTrue(prepare.isEnabled)
        code.typeText("5")
        XCTAssertFalse(prepare.isEnabled)
        code.typeText(XCUIKeyboardKey.delete.rawValue)
        XCTAssertTrue(prepare.isEnabled)
        if !prepare.isHittable { app.scrollViews.firstMatch.swipeUp() }
        prepare.tap()

        XCTAssertTrue(app.staticTexts["pairing-ready"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["pairing-countdown"].exists)
        XCTAssertFalse(code.exists)
        capture("05-pairing-ready-simulator", app)

        app.buttons["cancel-pairing"].tap()
        XCTAssertTrue(scan.waitForExistence(timeout: 5))
        useCode.tap()
        XCTAssertTrue(code.exists)
        XCTAssertFalse(prepare.isEnabled)
    }

    func testConfirmOrCancelReceivedMessageDeletion() {
        let app = start("received")
        app.tabBars.buttons["기록"].tap()
        let row = app.buttons["item-completed"]
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        let delete = app.buttons["delete-received-message"]
        if !delete.isHittable { app.scrollViews.firstMatch.swipeUp() }
        XCTAssertTrue(delete.isHittable)
        delete.tap()
        let confirmation = app.alerts.firstMatch
        XCTAssertTrue(confirmation.waitForExistence(timeout: 5))
        confirmation.buttons["취소"].tap()
        XCTAssertTrue(delete.exists)
        delete.tap()
        confirmation.buttons["메시지 삭제"].tap()
        XCTAssertTrue(app.navigationBars["기록"].waitForExistence(timeout: 5))
        XCTAssertFalse(row.exists)
    }

    func testDeleteSelectedReceivedMessagesFromHistory() {
        let app = start("received-multiple")
        app.tabBars.buttons["기록"].tap()
        let select = app.buttons["select-history-messages"]
        XCTAssertTrue(select.waitForExistence(timeout: 10))
        select.tap()

        let delete = app.buttons["delete-selected-messages"]
        XCTAssertFalse(delete.isEnabled)
        let selectable = app.buttons.matching(identifier: "select-received-message")
        XCTAssertEqual(selectable.count, 3)
        let first = selectable.matching(NSPredicate(format: "label CONTAINS %@", "message-alpha.txt")).firstMatch
        let second = selectable.matching(NSPredicate(format: "label CONTAINS %@", "message-beta.txt")).firstMatch
        XCTAssertTrue(first.exists)
        first.tap()
        XCTAssertTrue(first.isSelected)
        second.tap()
        let count = app.staticTexts["history-selection-count"]
        XCTAssertEqual(count.label, "2개 선택")
        XCTAssertTrue(delete.isEnabled)

        first.tap()
        XCTAssertFalse(first.isSelected)
        XCTAssertEqual(count.label, "1개 선택")
        first.tap()

        delete.tap()
        let confirmation = app.alerts.firstMatch
        XCTAssertTrue(confirmation.waitForExistence(timeout: 5))
        confirmation.buttons["취소"].tap()
        XCTAssertTrue(first.exists)
        XCTAssertTrue(second.exists)
        XCTAssertEqual(count.label, "2개 선택")

        delete.tap()
        XCTAssertTrue(confirmation.waitForExistence(timeout: 5))
        confirmation.buttons["선택한 메시지 삭제"].tap()
        XCTAssertTrue(select.waitForExistence(timeout: 5))
        XCTAssertFalse(count.exists)
        let records = app.buttons.matching(identifier: "item-completed")
        for name in ["message-alpha.txt", "message-beta.txt"] {
            XCTAssertFalse(records.matching(NSPredicate(format: "label CONTAINS %@", name)).firstMatch.exists)
        }
        for name in ["message-gamma.txt", "received-note.txt", "sent-message.txt"] {
            XCTAssertTrue(records.matching(NSPredicate(format: "label CONTAINS %@", name)).firstMatch.exists, name)
        }
    }

    private func start(_ state: String, dark: Bool = false, accessible: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--lab-synthetic", "--lab-state=\(state)", dark ? "--lab-dark" : "--lab-light"]
        if accessible {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        }
        app.launch()
        return app
    }

    private func capture(_ name: String, _ app: XCUIApplication) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
