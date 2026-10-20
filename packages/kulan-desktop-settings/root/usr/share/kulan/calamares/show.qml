/* Kulan OS installer slideshow (shown while files are copied). */
import QtQuick 2.0;
import calamares.slideshow 1.0;

Presentation
{
    id: presentation

    Timer {
        interval: 20000
        repeat: true
        onTriggered: presentation.goToNextSlide()
    }

    Slide {
        Image {
            id: background1
            source: "slide1.png"
            width: 467; height: 280
            fillMode: Image.PreserveAspectFit
            anchors.centerIn: parent
        }
        Text {
            anchors.horizontalCenter: background1.horizontalCenter
            anchors.top: background1.bottom
            anchors.topMargin: 12
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            width: background1.width
            text: qsTr("Welcome to Kulan OS.<br/>" +
                       "The rest of the installation is automatic and takes a few minutes.")
        }
    }
}
