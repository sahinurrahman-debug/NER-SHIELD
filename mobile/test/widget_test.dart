import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:mobile/main.dart';

void main() {
  testWidgets('App launches and shows the report form', (WidgetTester tester) async {
    await tester.pumpWidget(const NerShieldApp());

    expect(find.text('Report a hazard'), findsOneWidget);
    expect(find.byIcon(Icons.notifications_outlined), findsOneWidget);
  });
}
