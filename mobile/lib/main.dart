import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';

const apiBaseUrl = 'https://ner-shield-api.onrender.com';

void main() => runApp(const NerShieldApp());

class NerShieldApp extends StatelessWidget {
  const NerShieldApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'NER-SHIELD',
    theme: ThemeData(colorSchemeSeed: Colors.orange, useMaterial3: true),
    home: const HomeShell(),
  );
}

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int tab = 0;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: IndexedStack(index: tab, children: const [ReportPage(), AlertsPage()]),
    bottomNavigationBar: NavigationBar(
      selectedIndex: tab,
      onDestinationSelected: (i) => setState(() => tab = i),
      destinations: const [
        NavigationDestination(icon: Icon(Icons.report_outlined), label: 'Report'),
        NavigationDestination(icon: Icon(Icons.notifications_outlined), label: 'Alerts'),
      ],
    ),
  );
}

Future<String?> uploadMedia(XFile file) async {
  final extension = file.path.split('.').last.toLowerCase();
  final contentTypeByExt = {
    'jpg': MediaType('image', 'jpeg'), 'jpeg': MediaType('image', 'jpeg'),
    'png': MediaType('image', 'png'), 'webp': MediaType('image', 'webp'),
    'mp4': MediaType('video', 'mp4'), 'webm': MediaType('video', 'webm'),
  };
  final contentType = contentTypeByExt[extension];
  if (contentType == null) return null;
  try {
    final request = http.MultipartRequest('POST', Uri.parse('$apiBaseUrl/api/v1/uploads'));
    request.files.add(await http.MultipartFile.fromPath('file', file.path, contentType: contentType));
    final streamed = await request.send().timeout(const Duration(seconds: 30));
    if (streamed.statusCode != 200) return null;
    final body = jsonDecode(await streamed.stream.bytesToString()) as Map<String, dynamic>;
    return '$apiBaseUrl${body['url']}';
  } catch (_) {
    return null;
  }
}

class ReportPage extends StatefulWidget {
  const ReportPage({super.key});
  @override
  State<ReportPage> createState() => _ReportPageState();
}

class _ReportPageState extends State<ReportPage> {
  final description = TextEditingController();
  final district = TextEditingController(text: 'East Khasi Hills');
  String reportType = 'crack';
  String severity = 'moderate';
  String status = 'Ready';
  double? latitude;
  double? longitude;
  XFile? media;
  bool busy = false;

  Future<void> captureLocation() async {
    setState(() => status = 'Getting current location...');
    try {
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied || permission == LocationPermission.deniedForever) {
        setState(() => status = 'Location permission denied — using default coordinates.');
        return;
      }
      if (!await Geolocator.isLocationServiceEnabled()) {
        setState(() => status = 'Location services are off — using default coordinates.');
        return;
      }
      final position = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      ).timeout(const Duration(seconds: 15));
      setState(() {
        latitude = position.latitude;
        longitude = position.longitude;
        status = 'Location captured: ${latitude!.toStringAsFixed(5)}, ${longitude!.toStringAsFixed(5)}';
      });
    } catch (_) {
      setState(() => status = 'Could not get location — using default coordinates.');
    }
  }

  Future<void> pickMedia(ImageSource source, {bool video = false}) async {
    final picker = ImagePicker();
    final file = video ? await picker.pickVideo(source: source) : await picker.pickImage(source: source, imageQuality: 80);
    if (file != null) setState(() { media = file; status = 'Media attached: ${file.name}'; });
  }

  Map<String, dynamic> payload(String? imageUrl) => {
    'report_type': reportType, 'severity': severity, 'description': description.text,
    'latitude': latitude ?? 25.5788, 'longitude': longitude ?? 91.8933,
    'district': district.text.trim().isEmpty ? null : district.text.trim(),
    'road_status': 'restricted', 'reporter_role': 'citizen',
    if (imageUrl != null) 'image_url': imageUrl,
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
    setState(() => busy = true);
    String? imageUrl;
    if (media != null) {
      imageUrl = await uploadMedia(media!);
      if (imageUrl == null) status = 'Could not upload media (offline?) — submitting report without it.';
    }
    final item = payload(imageUrl);
    final sent = await send(item);
    if (!sent) await saveOffline(item);
    setState(() {
      busy = false;
      status = sent ? 'Report sent.' : 'Offline: report saved and will sync later.';
      if (sent) { description.clear(); media = null; }
    });
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
      DropdownButtonFormField(initialValue: reportType, items: const ['crack','slope_movement','landslide','blocked_road','rockfall','flooding']
        .map((v) => DropdownMenuItem(value: v, child: Text(v.replaceAll('_', ' ')))).toList(), onChanged: (v) => setState(() => reportType = v!)),
      const SizedBox(height: 12),
      DropdownButtonFormField(initialValue: severity, items: const ['low','moderate','high','critical']
        .map((v) => DropdownMenuItem(value: v, child: Text(v))).toList(), onChanged: (v) => setState(() => severity = v!)),
      const SizedBox(height: 12),
      TextField(controller: district, decoration: const InputDecoration(labelText: 'District', border: OutlineInputBorder())),
      const SizedBox(height: 12),
      TextField(controller: description, maxLines: 4, decoration: const InputDecoration(labelText: 'What did you observe?', border: OutlineInputBorder())),
      const SizedBox(height: 12),
      OutlinedButton.icon(onPressed: captureLocation, icon: const Icon(Icons.my_location), label: Text(
        latitude != null ? 'Location: ${latitude!.toStringAsFixed(4)}, ${longitude!.toStringAsFixed(4)}' : 'Capture current location',
      )),
      const SizedBox(height: 8),
      Row(children: [
        Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.camera), icon: const Icon(Icons.photo_camera), label: const Text('Photo'))),
        const SizedBox(width: 8),
        Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.gallery), icon: const Icon(Icons.photo_library), label: const Text('Gallery'))),
      ]),
      const SizedBox(height: 8),
      Row(children: [
        Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.camera, video: true), icon: const Icon(Icons.videocam), label: const Text('Video'))),
      ]),
      if (media != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text('Attached: ${media!.name}')),
      const SizedBox(height: 16),
      FilledButton(onPressed: busy ? null : submit, child: Text(busy ? 'Submitting…' : 'Submit report')),
      OutlinedButton(onPressed: busy ? null : syncQueue, child: const Text('Sync saved reports')),
      const SizedBox(height: 12), Text(status),
    ])),
  );
}

class AlertsPage extends StatefulWidget {
  const AlertsPage({super.key});
  @override
  State<AlertsPage> createState() => _AlertsPageState();
}

class _AlertsPageState extends State<AlertsPage> {
  List<dynamic> alerts = [];
  String status = 'Loading...';

  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() => status = 'Loading...');
    try {
      final response = await http.get(Uri.parse('$apiBaseUrl/api/v1/alerts')).timeout(const Duration(seconds: 10));
      if (response.statusCode == 200) {
        setState(() { alerts = jsonDecode(response.body) as List<dynamic>; status = alerts.isEmpty ? 'No alerts yet.' : ''; });
      } else {
        setState(() => status = 'Could not load alerts.');
      }
    } catch (_) {
      setState(() => status = 'Offline — could not reach the server.');
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Alerts'), actions: [IconButton(onPressed: refresh, icon: const Icon(Icons.refresh))]),
    body: RefreshIndicator(
      onRefresh: refresh,
      child: alerts.isEmpty
          ? ListView(children: [Padding(padding: const EdgeInsets.all(24), child: Text(status))])
          : ListView.builder(
              itemCount: alerts.length,
              itemBuilder: (context, i) {
                final a = alerts[i] as Map<String, dynamic>;
                return ListTile(
                  leading: Icon(Icons.warning, color: {
                    'critical': Colors.red, 'high': Colors.orange, 'moderate': Colors.amber, 'low': Colors.green,
                  }[a['severity']] ?? Colors.grey),
                  title: Text('${(a['severity'] as String).toUpperCase()} · ${a['district'] ?? 'Unknown location'}'),
                  subtitle: Text(a['message'] as String),
                );
              },
            ),
    ),
  );
}
