import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

const apiBaseUrl = 'http://10.0.2.2:8000'; // Android emulator only

void main() => runApp(const PaharSathiApp());

class PaharSathiApp extends StatelessWidget {
  const PaharSathiApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'PaharSathi AI', theme: ThemeData(colorSchemeSeed: Colors.orange, useMaterial3: true), home: const ReportPage(),
  );
}

class ReportPage extends StatefulWidget {
  const ReportPage({super.key});
  @override
  State<ReportPage> createState() => _ReportPageState();
}

class _ReportPageState extends State<ReportPage> {
  final description = TextEditingController();
  String reportType = 'crack';
  String severity = 'moderate';
  String status = 'Ready';

  Map<String, dynamic> payload() => {
    'report_type': reportType, 'severity': severity, 'description': description.text,
    'latitude': 25.5788, 'longitude': 91.8933, // Replace with permission-based GPS next.
    'district': 'East Khasi Hills', 'road_status': 'restricted', 'reporter_role': 'citizen',
  };

  Future<void> saveOffline(Map<String, dynamic> item) async {
    final prefs = await SharedPreferences.getInstance();
    final queue = prefs.getStringList('report_queue') ?? [];
    queue.add(jsonEncode(item));
    await prefs.setStringList('report_queue', queue);
  }

  Future<bool> send(Map<String, dynamic> item) async {
    try {
      final response = await http.post(Uri.parse('$apiBaseUrl/api/v1/reports'),
          headers: {'Content-Type': 'application/json'}, body: jsonEncode(item)).timeout(const Duration(seconds: 10));
      return response.statusCode == 201;
    } catch (_) { return false; }
  }

  Future<void> submit() async {
    if (description.text.trim().length < 3) { setState(() => status = 'Describe the hazard in at least 3 characters.'); return; }
    final item = payload();
    final sent = await send(item);
    if (!sent) await saveOffline(item);
    setState(() { status = sent ? 'Report sent.' : 'Offline: report saved and will sync later.'; if (sent) description.clear(); });
  }

  Future<void> syncQueue() async {
    final prefs = await SharedPreferences.getInstance();
    final queue = prefs.getStringList('report_queue') ?? [];
    final remaining = <String>[];
    for (final raw in queue) { if (!await send(jsonDecode(raw))) remaining.add(raw); }
    await prefs.setStringList('report_queue', remaining);
    setState(() => status = 'Sync finished. ${remaining.length} report(s) still queued.');
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Report a hazard')),
    body: Padding(padding: const EdgeInsets.all(16), child: ListView(children: [
      DropdownButtonFormField(value: reportType, items: const ['crack','slope_movement','landslide','blocked_road','rockfall','flooding']
        .map((v) => DropdownMenuItem(value: v, child: Text(v.replaceAll('_', ' ')))).toList(), onChanged: (v) => setState(() => reportType = v!)),
      const SizedBox(height: 12),
      DropdownButtonFormField(value: severity, items: const ['low','moderate','high','critical']
        .map((v) => DropdownMenuItem(value: v, child: Text(v))).toList(), onChanged: (v) => setState(() => severity = v!)),
      const SizedBox(height: 12), TextField(controller: description, maxLines: 4, decoration: const InputDecoration(labelText: 'What did you observe?', border: OutlineInputBorder())),
      const SizedBox(height: 12), FilledButton(onPressed: submit, child: const Text('Submit report')), OutlinedButton(onPressed: syncQueue, child: const Text('Sync saved reports')),
      const SizedBox(height: 12), Text(status),
    ])),
  );
}