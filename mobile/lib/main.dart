import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'strings.dart';

const apiBaseUrl = 'https://ner-shield-api.onrender.com';

void main() => runApp(const NerShieldApp());

class NerShieldApp extends StatefulWidget {
  const NerShieldApp({super.key});
  @override
  State<NerShieldApp> createState() => _NerShieldAppState();
}

class _NerShieldAppState extends State<NerShieldApp> {
  String language = 'en';

  @override
  void initState() {
    super.initState();
    SharedPreferences.getInstance().then((prefs) {
      final saved = prefs.getString('app_language');
      if (saved != null && supportedLanguages.contains(saved)) setState(() => language = saved);
    });
  }

  Future<void> setLanguage(String lang) async {
    setState(() => language = lang);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('app_language', lang);
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'NER-SHIELD',
    theme: ThemeData(colorSchemeSeed: Colors.orange, useMaterial3: true),
    home: HomeShell(language: language, onLanguageChanged: setLanguage),
  );
}

class HomeShell extends StatefulWidget {
  final String language;
  final ValueChanged<String> onLanguageChanged;
  const HomeShell({super.key, required this.language, required this.onLanguageChanged});
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int tab = 0;

  @override
  Widget build(BuildContext context) {
    final lang = widget.language;
    return Scaffold(
      appBar: AppBar(
        title: Text(tab == 0 ? t(lang, 'report_appbar') : t(lang, 'alerts_appbar')),
        actions: [
          PopupMenuButton<String>(
            icon: const Icon(Icons.language),
            tooltip: t(lang, 'language'),
            onSelected: widget.onLanguageChanged,
            itemBuilder: (context) => supportedLanguages
                .map((code) => PopupMenuItem(value: code, child: Text(languageNames[code] ?? code)))
                .toList(),
          ),
          if (tab == 1) IconButton(onPressed: () => _alertsKey.currentState?.refresh(), icon: const Icon(Icons.refresh)),
        ],
      ),
      body: IndexedStack(index: tab, children: [
        ReportPage(language: lang),
        AlertsPage(key: _alertsKey, language: lang),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab,
        onDestinationSelected: (i) => setState(() => tab = i),
        destinations: [
          NavigationDestination(icon: const Icon(Icons.report_outlined), label: t(lang, 'nav_report')),
          NavigationDestination(icon: const Icon(Icons.notifications_outlined), label: t(lang, 'nav_alerts')),
        ],
      ),
    );
  }

  final _alertsKey = GlobalKey<_AlertsPageState>();
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
  final String language;
  const ReportPage({super.key, required this.language});
  @override
  State<ReportPage> createState() => _ReportPageState();
}

class _ReportPageState extends State<ReportPage> {
  final description = TextEditingController();
  final district = TextEditingController(text: 'East Khasi Hills');
  String reportType = 'crack';
  String severity = 'moderate';
  String? status;
  double? latitude;
  double? longitude;
  XFile? media;
  bool busy = false;

  String get lang => widget.language;

  Future<void> captureLocation() async {
    setState(() => status = t(lang, 'getting_location'));
    try {
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied || permission == LocationPermission.deniedForever) {
        setState(() => status = t(lang, 'location_denied'));
        return;
      }
      if (!await Geolocator.isLocationServiceEnabled()) {
        setState(() => status = t(lang, 'location_off'));
        return;
      }
      final position = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      ).timeout(const Duration(seconds: 15));
      setState(() {
        latitude = position.latitude;
        longitude = position.longitude;
        status = '${t(lang, 'location_captured')}: ${latitude!.toStringAsFixed(5)}, ${longitude!.toStringAsFixed(5)}';
      });
    } catch (_) {
      setState(() => status = t(lang, 'location_failed'));
    }
  }

  Future<void> pickMedia(ImageSource source, {bool video = false}) async {
    final picker = ImagePicker();
    final file = video ? await picker.pickVideo(source: source) : await picker.pickImage(source: source, imageQuality: 80);
    if (file != null) setState(() { media = file; status = '${t(lang, 'media_attached')}: ${file.name}'; });
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
    if (description.text.trim().length < 3) { setState(() => status = t(lang, 'describe_min')); return; }
    setState(() => busy = true);
    String? imageUrl;
    if (media != null) {
      imageUrl = await uploadMedia(media!);
      if (imageUrl == null) status = t(lang, 'upload_failed');
    }
    final item = payload(imageUrl);
    final sent = await send(item);
    if (!sent) await saveOffline(item);
    setState(() {
      busy = false;
      status = sent ? t(lang, 'report_sent') : t(lang, 'report_offline');
      if (sent) { description.clear(); media = null; }
    });
  }

  Future<void> syncQueue() async {
    final prefs = await SharedPreferences.getInstance();
    final queue = prefs.getStringList('report_queue') ?? [];
    final remaining = <String>[];
    for (final raw in queue) { if (!await send(jsonDecode(raw))) remaining.add(raw); }
    await prefs.setStringList('report_queue', remaining);
    setState(() => status = '${t(lang, 'sync_finished')} ${remaining.length} ${t(lang, 'still_queued')}');
  }

  @override
  Widget build(BuildContext context) => Padding(padding: const EdgeInsets.all(16), child: ListView(children: [
    DropdownButtonFormField(initialValue: reportType, items: ['crack','slope_movement','landslide','blocked_road','rockfall','flooding']
      .map((v) => DropdownMenuItem(value: v, child: Text(t(lang, v)))).toList(), onChanged: (v) => setState(() => reportType = v!)),
    const SizedBox(height: 12),
    DropdownButtonFormField(initialValue: severity, items: ['low','moderate','high','critical']
      .map((v) => DropdownMenuItem(value: v, child: Text(t(lang, v)))).toList(), onChanged: (v) => setState(() => severity = v!)),
    const SizedBox(height: 12),
    TextField(controller: district, decoration: InputDecoration(labelText: t(lang, 'district_label'), border: const OutlineInputBorder())),
    const SizedBox(height: 12),
    TextField(controller: description, maxLines: 4, decoration: InputDecoration(labelText: t(lang, 'description_label'), border: const OutlineInputBorder())),
    const SizedBox(height: 12),
    OutlinedButton.icon(onPressed: captureLocation, icon: const Icon(Icons.my_location), label: Text(
      latitude != null ? '${t(lang, 'location_prefix')}: ${latitude!.toStringAsFixed(4)}, ${longitude!.toStringAsFixed(4)}' : t(lang, 'capture_location'),
    )),
    const SizedBox(height: 8),
    Row(children: [
      Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.camera), icon: const Icon(Icons.photo_camera), label: Text(t(lang, 'photo')))),
      const SizedBox(width: 8),
      Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.gallery), icon: const Icon(Icons.photo_library), label: Text(t(lang, 'gallery')))),
    ]),
    const SizedBox(height: 8),
    Row(children: [
      Expanded(child: OutlinedButton.icon(onPressed: () => pickMedia(ImageSource.camera, video: true), icon: const Icon(Icons.videocam), label: Text(t(lang, 'video')))),
    ]),
    if (media != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text('${t(lang, 'attached')}: ${media!.name}')),
    const SizedBox(height: 16),
    FilledButton(onPressed: busy ? null : submit, child: Text(busy ? t(lang, 'submitting') : t(lang, 'submit'))),
    OutlinedButton(onPressed: busy ? null : syncQueue, child: Text(t(lang, 'sync'))),
    const SizedBox(height: 12), Text(status ?? t(lang, 'ready')),
  ]));
}

class AlertsPage extends StatefulWidget {
  final String language;
  const AlertsPage({super.key, required this.language});
  @override
  State<AlertsPage> createState() => _AlertsPageState();
}

class _AlertsPageState extends State<AlertsPage> {
  List<dynamic> alerts = [];
  String? status;

  String get lang => widget.language;

  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() => status = t(lang, 'loading'));
    try {
      final response = await http.get(Uri.parse('$apiBaseUrl/api/v1/alerts')).timeout(const Duration(seconds: 10));
      if (response.statusCode == 200) {
        setState(() { alerts = jsonDecode(response.body) as List<dynamic>; status = alerts.isEmpty ? t(lang, 'no_alerts') : null; });
      } else {
        setState(() => status = t(lang, 'alerts_load_error'));
      }
    } catch (_) {
      setState(() => status = t(lang, 'alerts_offline'));
    }
  }

  @override
  Widget build(BuildContext context) => RefreshIndicator(
    onRefresh: refresh,
    child: alerts.isEmpty
        ? ListView(children: [Padding(padding: const EdgeInsets.all(24), child: Text(status ?? ''))])
        : ListView.builder(
            itemCount: alerts.length,
            itemBuilder: (context, i) {
              final a = alerts[i] as Map<String, dynamic>;
              return ListTile(
                leading: Icon(Icons.warning, color: {
                  'critical': Colors.red, 'high': Colors.orange, 'moderate': Colors.amber, 'low': Colors.green,
                }[a['severity']] ?? Colors.grey),
                title: Text('${(a['severity'] as String).toUpperCase()} · ${a['district'] ?? t(lang, 'unknown_location')}'),
                subtitle: Text(a['message'] as String),
              );
            },
          ),
  );
}
