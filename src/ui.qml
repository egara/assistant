import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    visible: true
    width: 650
    height: 800
    title: "Assistant AI"
    color: "#0f141c"

    onClosing: function(close) {
        close.accepted = false;
        window.hide();
    }

    property int activeAiIndex: -1
    property bool userScrolledUp: false
    property bool isVoiceActive: false
    readonly property string monoFont: "JetBrainsMono Nerd Font Mono"
    readonly property int systemFontSize: window.font.pixelSize > 0 ? window.font.pixelSize : (window.font.pointSize > 0 ? Math.round(window.font.pointSize * 1.33) : 14)

    Component.onCompleted: {
        inputBox.forceActiveFocus();
    }

    Connections {
        target: backend
        function onActiveModelChanged(modelName) {
            modelLabel.text = modelName;
        }
        function onVoiceStateChanged(active) {
            isVoiceActive = active;
        }
        function onStatusChanged(text, colorHex) {
            statusLabel.text = text;
            statusLabel.color = colorHex;
        }
        function onMessageAdded(role, msgText) {
            userScrolledUp = false;
            chatModel.append({
                "role": role,
                "msgText": msgText
            });

            if (role === "ai") {
                activeAiIndex = chatModel.count - 1;
            }

            chatListView.positionViewAtEnd();
        }
        function onMessageAppended(newText) {
            if (activeAiIndex >= 0 && activeAiIndex < chatModel.count) {
                var item = chatModel.get(activeAiIndex);
                chatModel.setProperty(activeAiIndex, "msgText", item.msgText + newText);
                if (!userScrolledUp) {
                    chatListView.positionViewAtEnd();
                }
            }
        }
        function onInputStateChanged(enabled) {
            inputBox.enabled = enabled;
            if (enabled) {
                userScrolledUp = false;
            }
        }
        function onStopButtonStateChanged(visible) {
            stopButton.visible = visible;
        }

        function onRequireApproval(command) {
            userScrolledUp = false;
            chatModel.append({
                "role": "approval",
                "msgText": command
            });
            chatListView.positionViewAtEnd();
        }
        function onClearApproval() {
            if (chatModel.count > 0 && chatModel.get(chatModel.count - 1).role === "approval") {
                chatModel.remove(chatModel.count - 1);
            }
        }
        function onChatCleared() {
            chatModel.clear();
            activeAiIndex = -1;
            userScrolledUp = false;
        }
    }

    ListModel {
        id: chatModel
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        // Top Status Pill
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            height: 28
            radius: 14
            color: "#181d28"
            border.color: "#283040"
            border.width: 1
            implicitWidth: statusLayout.implicitWidth + 24

            RowLayout {
                id: statusLayout
                anchors.centerIn: parent
                spacing: 8

                Rectangle {
                    width: 7
                    height: 7
                    radius: 4
                    color: statusLabel.color
                    Layout.alignment: Qt.AlignVCenter
                }

                Label {
                    id: statusLabel
                    text: "Idle"
                    color: "#828c99"
                    font.family: window.monoFont
                    font.pixelSize: 12
                    font.bold: true
                    Layout.alignment: Qt.AlignVCenter
                }
            }
        }

        // Chat View
        ListView {
            id: chatListView
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: chatModel
            spacing: 12
            clip: true
            footer: Item {
                width: chatListView.width
                height: 12
            }

            onMovementStarted: {
                userScrolledUp = true;
            }

            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded
                contentItem: Rectangle {
                    color: "#2d3648"
                    radius: 4
                }
            }

            delegate: Item {
                id: delegateRoot
                width: chatListView.width
                height: bubble.height + 6

                Text {
                    id: textMetric
                    text: role === "approval" ? "󰀦 **Action Required**\n\nThe AI wants to execute an unverified command:\n\n`" + msgText + "`" : msgText
                    textFormat: Text.MarkdownText
                    font.family: window.monoFont
                    font.pixelSize: window.systemFontSize
                    visible: false
                }

                Rectangle {
                    id: bubble
                    y: 3

                    anchors.right: role === "user" ? parent.right : undefined
                    anchors.left: (role === "ai" || role === "approval") ? parent.left : undefined
                    anchors.horizontalCenter: role === "sys" ? parent.horizontalCenter : undefined

                    width: contentCol.width + 30
                    height: contentCol.height + 22

                    color: {
                        if (role === "user")
                            return "#223147";
                        if (role === "ai" || role === "approval")
                            return "#161b24";
                        if (role === "sys")
                            return msgText.indexOf("Error") !== -1 || msgText.indexOf("⚠️") !== -1 || msgText.indexOf("󰅚") !== -1 ? "#341a22" : "#141c24";
                        return "#161b24";
                    }
                    radius: role === "sys" ? 12 : 16

                    border.color: {
                        if (role === "user")
                            return "#59c2ff";
                        if (role === "approval")
                            return "#e5b454";
                        if (role === "sys")
                            return msgText.indexOf("Error") !== -1 || msgText.indexOf("⚠️") !== -1 || msgText.indexOf("󰅚") !== -1 ? "#f07178" : "#283446";
                        return "#232a38";
                    }
                    border.width: 1

                    Column {
                        id: contentCol
                        anchors.centerIn: parent
                        spacing: 10

                        property real maxWidth: delegateRoot.width * 0.85 - 30

                        TextEdit {
                            id: msgTextItem
                            width: Math.min(textMetric.implicitWidth, contentCol.maxWidth)

                            text: textMetric.text
                            color: {
                                if (role === "user")
                                    return "#e6e1cf";
                                if (role === "ai" || role === "approval")
                                    return "#e6e1cf";
                                if (msgText.indexOf("Error") !== -1 || msgText.indexOf("⚠️") !== -1 || msgText.indexOf("󰅚") !== -1)
                                    return "#f07178";
                                return "#7fd962";
                            }
                            wrapMode: TextEdit.Wrap
                            textFormat: TextEdit.MarkdownText
                            readOnly: true
                            selectByMouse: true
                            font.family: window.monoFont
                            font.pixelSize: textMetric.font.pixelSize
                            selectedTextColor: "#ffffff"
                            selectionColor: "#3e4b59"
                            onLinkActivated: link => Qt.openUrlExternally(link)
                        }

                        Row {
                            visible: role === "approval"
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: 12

                            Button {
                                id: approveBtn
                                text: "󰄬 Approve"
                                onClicked: backend.resolveCommandApproval(true)
                                background: Rectangle {
                                    color: approveBtn.down ? "#6bc052" : (approveBtn.hovered ? "#91e675" : "#7fd962")
                                    radius: 10
                                    implicitWidth: 100
                                    implicitHeight: 34
                                }
                                contentItem: Text {
                                    text: parent.text
                                    color: "#0f141c"
                                    font.family: window.monoFont
                                    font.bold: true
                                    font.pixelSize: 13
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }

                            Button {
                                id: denyBtn
                                text: "󰅖 Deny"
                                onClicked: backend.resolveCommandApproval(false)
                                background: Rectangle {
                                    color: denyBtn.down ? "#d25a62" : (denyBtn.hovered ? "#ff858d" : "#f07178")
                                    radius: 10
                                    implicitWidth: 100
                                    implicitHeight: 34
                                }
                                contentItem: Text {
                                    text: parent.text
                                    color: "#0f141c"
                                    font.family: window.monoFont
                                    font.bold: true
                                    font.pixelSize: 13
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }
                }
            }
        }

        // Bottom Controls Bar
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            // Help Button
            Button {
                id: helpButton
                text: "󰞋"
                Layout.preferredWidth: 54
                Layout.preferredHeight: 54
                ToolTip.visible: hovered
                ToolTip.text: "Ayuda y comandos (/new, /clear, /help)"
                background: Rectangle {
                    color: parent.down ? "#283446" : (parent.hovered ? "#222a38" : "#181d28")
                    radius: 14
                    border.color: parent.hovered ? "#59c2ff" : "#283040"
                    border.width: 1
                }
                contentItem: Text {
                    text: parent.text
                    color: parent.hovered ? "#59c2ff" : "#828c99"
                    font.family: window.monoFont
                    font.pixelSize: 22
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: backend.show_help()
            }

            // Text Input Box Container
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 54
                background: Rectangle {
                    color: "#181d28"
                    radius: 14
                    border.color: inputBox.activeFocus ? "#59c2ff" : "#283040"
                    border.width: inputBox.activeFocus ? 1.5 : 1
                }

                TextArea {
                    id: inputBox
                    placeholderText: "Escribe un mensaje o pulsa el micro..."
                    placeholderTextColor: "#4f5a6b"
                    color: "#e6e1cf"
                    font.family: window.monoFont
                    font.pixelSize: window.systemFontSize
                    wrapMode: TextArea.Wrap
                    padding: 12

                    Keys.onPressed: event => {
                        if (event.key === Qt.Key_Return && !event.modifiers) {
                            var txt = text.trim();
                            if (txt !== "") {
                                if (txt.toLowerCase() === "v")
                                    backend.toggleVoiceRecording();
                                else
                                    backend.processInput(txt);
                                text = "";
                            }
                            event.accepted = true;
                        }
                    }
                }
            }

            // Mic Button
            Button {
                id: micButton
                text: isVoiceActive ? "󰍬" : "󰍭"
                Layout.preferredWidth: 54
                Layout.preferredHeight: 54
                ToolTip.visible: hovered
                ToolTip.text: isVoiceActive ? "Detener grabación de voz" : "Activar modo de voz (o pulsa 'v')"
                background: Rectangle {
                    color: isVoiceActive ? "#2b3d2b" : (parent.down ? "#283446" : (parent.hovered ? "#222a38" : "#181d28"))
                    radius: 14
                    border.color: isVoiceActive ? "#7fd962" : (parent.hovered ? "#59c2ff" : "#283040")
                    border.width: 1
                }
                contentItem: Text {
                    text: parent.text
                    color: isVoiceActive ? "#7fd962" : (parent.hovered ? "#59c2ff" : "#828c99")
                    font.family: window.monoFont
                    font.pixelSize: 22
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: backend.toggleVoiceRecording()
            }

            // Stop AI Operation Button
            Button {
                id: stopButton
                text: "󰓛"
                visible: false
                Layout.preferredWidth: 54
                Layout.preferredHeight: 54
                ToolTip.visible: hovered
                ToolTip.text: "Detener generación"
                background: Rectangle {
                    color: parent.down ? "#5c2328" : (parent.hovered ? "#471c21" : "#341a22")
                    radius: 14
                    border.color: parent.hovered ? "#f07178" : "#54252d"
                    border.width: 1
                }
                contentItem: Text {
                    text: parent.text
                    color: "#f07178"
                    font.family: window.monoFont
                    font.pixelSize: 22
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: backend.stopAIOperation()
            }
        }

        // Active Model Pill Badge
        Rectangle {
            id: modelBadge
            visible: modelLabel.text !== ""
            Layout.alignment: Qt.AlignHCenter
            height: 22
            radius: 11
            color: "#141822"
            border.color: "#232a38"
            border.width: 1
            implicitWidth: modelLabel.implicitWidth + 20

            Label {
                id: modelLabel
                anchors.centerIn: parent
                text: ""
                color: "#606b7a"
                font.family: window.monoFont
                font.pixelSize: 11
                font.bold: false
            }
        }
    }
}
