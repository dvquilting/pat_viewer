#include "qpv/license_dialog.hpp"

#include "qpv/license.hpp"

#include <QDialog>
#include <QDialogButtonBox>
#include <QFormLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QVBoxLayout>

namespace qpv {
namespace {

QString purchaseLink() {
    if (license::purchase_url.empty()) return {};
    const auto url = QString::fromUtf8(license::purchase_url.data(),
                                       static_cast<qsizetype>(license::purchase_url.size()));
    return QObject::tr("<a href=\"%1\">Buy a license</a>").arg(url.toHtmlEscaped());
}

QString statusText(const license::Status& status) {
    return QString::fromStdString(license::describe(status));
}

} // namespace

bool runLicenseDialog(QWidget* parent, const QString& reason) {
    const auto status = license::status();
    QDialog dialog(parent);
    dialog.setWindowTitle(QObject::tr("License"));
    dialog.resize(520, 0);
    auto* layout = new QVBoxLayout(&dialog);
    if (!reason.isEmpty()) {
        auto* why = new QLabel(reason);
        why->setWordWrap(true);
        layout->addWidget(why);
    }
    layout->addWidget(new QLabel("<b>" + statusText(status).toHtmlEscaped() + "</b>"));
    if (const auto link = purchaseLink(); !link.isEmpty() && status.state != license::State::Licensed) {
        auto* buy = new QLabel(link);
        buy->setOpenExternalLinks(true);
        layout->addWidget(buy);
    }

    auto* form = new QFormLayout;
    auto* name = new QLineEdit;
    name->setPlaceholderText(QObject::tr("Exactly as it appears with your key"));
    auto* key = new QPlainTextEdit;
    key->setPlaceholderText(QObject::tr("QPV1-…"));
    key->setTabChangesFocus(true);
    key->setFixedHeight(key->fontMetrics().lineSpacing() * 4 + 12);
    form->addRow(QObject::tr("Licensed to (name or email):"), name);
    form->addRow(QObject::tr("License key:"), key);
    layout->addLayout(form);
    auto* error = new QLabel;
    error->setWordWrap(true);
    error->setStyleSheet("color: #b00020;");
    error->hide();
    layout->addWidget(error);

    auto* buttons = new QDialogButtonBox;
    auto* activate = buttons->addButton(QObject::tr("Activate"), QDialogButtonBox::AcceptRole);
    buttons->addButton(QDialogButtonBox::Close);
    QPushButton* remove = nullptr;
    if (status.state == license::State::Licensed) {
        name->setText(QString::fromStdString(status.licensee));
        remove = buttons->addButton(QObject::tr("Remove License…"), QDialogButtonBox::ResetRole);
    }
    activate->setDefault(true);
    layout->addWidget(buttons);

    bool changed = false;
    QObject::connect(activate, &QPushButton::clicked, &dialog, [&] {
        const auto message = license::install(name->text().toStdString(),
                                              key->toPlainText().toStdString());
        if (!message.empty()) {
            error->setText(QString::fromStdString(message));
            error->show();
            return;
        }
        changed = true;
        QMessageBox::information(&dialog, QObject::tr("License"),
            QObject::tr("Thank you. This copy is now licensed to %1.")
                .arg(QString::fromStdString(license::status().licensee)));
        dialog.accept();
    });
    if (remove) QObject::connect(remove, &QPushButton::clicked, &dialog, [&] {
        if (QMessageBox::question(&dialog, QObject::tr("Remove License"),
                QObject::tr("Remove the license from this computer? You can enter the key "
                            "again at any time.")) != QMessageBox::Yes)
            return;
        license::uninstall();
        changed = true;
        dialog.accept();
    });
    QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    dialog.exec();
    return changed;
}

bool offerLicense(QWidget* parent, const QString& feature) {
    if (license::status().unlocked()) return true;
    QString text = QObject::tr("%1 needs a license now that the %2-day trial has ended. "
                               "Opening and viewing patterns remains free.")
                       .arg(feature).arg(license::trial_length_days);
    if (const auto link = purchaseLink(); !link.isEmpty()) text += "<br><br>" + link;
    QMessageBox box(QMessageBox::Information, QObject::tr("License Needed"), text,
                    QMessageBox::NoButton, parent);
    box.setTextFormat(Qt::RichText);
    box.setTextInteractionFlags(Qt::TextBrowserInteraction);
    auto* enter = box.addButton(QObject::tr("Enter License Key…"), QMessageBox::AcceptRole);
    box.addButton(QMessageBox::Close);
    box.setDefaultButton(enter);
    box.exec();
    if (box.clickedButton() != enter) return false;
    runLicenseDialog(parent);
    return license::status().unlocked();
}

} // namespace qpv
