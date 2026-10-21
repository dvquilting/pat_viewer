#pragma once

#include <QString>

class QWidget;

namespace qpv {

// Shows the license state and lets the user enter or remove a license key.
// `reason`, when given, says which feature asked. Returns true when the
// license changed.
bool runLicenseDialog(QWidget* parent, const QString& reason = {});

// The message for a feature that needs a license, offering to enter one.
// Returns true when the feature may now be used.
bool offerLicense(QWidget* parent, const QString& feature);

} // namespace qpv
