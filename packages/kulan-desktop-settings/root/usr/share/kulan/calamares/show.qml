/* Kulan OS installer slideshow, shown while files are copied: a short tour
   of what's waiting after the restart. One slide every 15 seconds. */
import QtQuick 2.0;
import calamares.slideshow 1.0;

Presentation
{
    id: presentation

    Timer {
        interval: 15000
        running: true
        repeat: true
        onTriggered: presentation.goToNextSlide()
    }

    /* image, title and text, centred; the image shrinks on small screens */
    component KulanSlide: Slide {
        id: slide
        property alias image: picture.source
        property alias headline: heading.text
        property alias body: words.text

        Column {
            anchors.centerIn: parent
            width: Math.min(slide.width * 0.9, 620)
            spacing: 12

            Image {
                id: picture
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(parent.width, 560)
                height: Math.min(slide.height * 0.62, 412)
                fillMode: Image.PreserveAspectFit
                smooth: true
            }
            Text {
                id: heading
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                font.pixelSize: 22
                font.bold: true
                wrapMode: Text.WordWrap
            }
            Text {
                id: words
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                font.pixelSize: 14
                wrapMode: Text.WordWrap
                opacity: 0.8
            }
        }
    }

    KulanSlide {
        image: "slide1.png"
        headline: qsTr("Welcome to Kulan OS")
        body: qsTr("The rest of the installation is automatic and takes a few minutes.")
    }
    KulanSlide {
        image: "slide-look.png"
        headline: qsTr("Make it yours")
        body: qsTr("Light, dark or automatic. Choose your accent colour, dock and window buttons in Kulan Settings.")
    }
    KulanSlide {
        image: "slide-privacy.png"
        headline: qsTr("Private and protected")
        body: qsTr("A firewall, disk encryption and daily security fixes. Block ads and trackers in every app with one switch.")
    }
    KulanSlide {
        image: "slide-shortcuts.png"
        headline: qsTr("Feels like a Mac")
        body: qsTr("Super+Space to search, Super+Shift+4 for a screenshot, plus Live Text, window snapping and Quick Look.")
    }
    KulanSlide {
        image: "slide-addons.png"
        headline: qsTr("Add more in one click")
        body: qsTr("The Developer Kit, office apps, Phone Link, a virus scanner and more, whenever you want them.")
    }
    KulanSlide {
        image: "slide1.png"
        headline: qsTr("Almost there")
        body: qsTr("After the restart, Setup Assistant helps you bring over your files and add your apps.")
    }
}
