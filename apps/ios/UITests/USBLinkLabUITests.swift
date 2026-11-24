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
