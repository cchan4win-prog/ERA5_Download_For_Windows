using System;
using System.IO;
using System.Text;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.Threading;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Windows.Forms;

sealed class DownloaderWindow : Form {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr FindWindow(string className, string windowName);
    [DllImport("user32.dll")]
    static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr window);
    readonly string root = AppDomain.CurrentDomain.BaseDirectory;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8000000 };
    readonly Label heading = new Label(), summary = new Label(), rate = new Label(), timing = new Label(), taskInfo = new Label();
    readonly ListView queueView = new ListView();
    readonly ProgressBar overall = new ProgressBar();
    readonly List<Label> fileLabels = new List<Label>();
    readonly List<ProgressBar> fileBars = new List<ProgressBar>();
    readonly TextBox log = new TextBox();
    readonly Button pause = new Button(), resume = new Button(), start = new Button(), cancel = new Button(), config = new Button();
    readonly Panel files = new Panel { AutoScroll = true };
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    readonly object logLock = new object();
    Process worker;
    string session, statusPath, controlPath, dataFolder;
    bool closing, commandPaused, cancelling, merging;
    DateTime cancelRequested;
    double previousBytes;
    DateTime previousSample = DateTime.UtcNow;
    readonly bool preview;

    public DownloaderWindow(bool previewMode) {
        preview = previewMode;
        Text = "ERA5-reanalysis-complete下载器v2.0";
        ClientSize = new Size(1000, 840);
        MinimumSize = new Size(1016, 879);
        Font = new Font("Microsoft YaHei UI", 10F);
        BackColor = Color.FromArgb(241, 245, 250);
        StartPosition = FormStartPosition.CenterScreen;
        heading.Text = "ERA5-reanalysis-complete下载器v2.0";
        heading.Font = new Font(Font.FontFamily, 19F, FontStyle.Bold);
        heading.ForeColor = Color.FromArgb(24, 42, 69);
        heading.SetBounds(28, 20, 944, 42);
        Controls.Add(heading);
        summary.Text = "就绪 · 点击“开始”读取配置并下载";
        summary.SetBounds(30, 72, 940, 27); Controls.Add(summary);
        overall.SetBounds(30, 108, 940, 16); Controls.Add(overall);
        rate.Text = "逐小时保存 · 断点续传 · 下载齐全后自动合并";
        rate.SetBounds(30, 134, 940, 25); Controls.Add(rate);
        timing.Text = "已用时 00:00:00    ·    预计还需 —";
        timing.ForeColor = Color.FromArgb(40, 85, 145);
        timing.SetBounds(30, 164, 940, 26); Controls.Add(timing);
        taskInfo.Text = "可在配置中设置多个站点、多个时间段，按顺序自动处理";
        taskInfo.SetBounds(30, 202, 940, 26); Controls.Add(taskInfo);
        queueView.View = View.Details; queueView.FullRowSelect = true; queueView.GridLines = false;
        queueView.HeaderStyle = ColumnHeaderStyle.Nonclickable; queueView.MultiSelect = false;
        queueView.Columns.Add("站点", 145); queueView.Columns.Add("时间段", 390);
        queueView.Columns.Add("小时进度", 180); queueView.Columns.Add("状态", 180);
        queueView.SetBounds(30, 236, 940, 128); Controls.Add(queueView);
        files.BackColor = Color.White;
        files.SetBounds(30, 382, 940, 214); Controls.Add(files);
        ConfigureSlots(3);
        start.Text = "开始"; start.SetBounds(30, 614, 170, 42);
        cancel.Text = "取消"; cancel.SetBounds(222, 614, 170, 42);
        pause.Text = "暂停"; pause.SetBounds(414, 614, 170, 42);
        resume.Text = "继续"; resume.SetBounds(606, 614, 170, 42);
        config.Text = "配置参数"; config.SetBounds(798, 614, 172, 42);
        Controls.AddRange(new Control[] { start, cancel, pause, resume, config });
        foreach (Button button in new Button[] {start,cancel,pause,resume,config}) {
            button.FlatStyle = FlatStyle.Flat; button.FlatAppearance.BorderColor = Color.FromArgb(204,215,230);
            button.BackColor = Color.White; button.ForeColor = Color.FromArgb(30,50,80);
        }
        start.BackColor = Color.FromArgb(40,100,200); start.ForeColor = Color.White;
        cancel.ForeColor = Color.FromArgb(160,60,60);
        start.Click += delegate { StartDownload(); };
        cancel.Click += delegate { Send("stop"); };
        pause.Click += delegate { Send("pause"); }; resume.Click += delegate { Send("run"); };
        config.Click += delegate {
            try { using(var editor=new ConfigurationWindow(root)) { if(editor.ShowDialog(this)==DialogResult.OK) AddLog("可视化配置已保存，点击“开始”读取新配置。"); } }
            catch(Exception e){AddLog("无法打开配置窗口："+e.Message);}
        };
        log.Multiline = true; log.ReadOnly = true; log.ScrollBars = ScrollBars.Vertical;
        log.BackColor = Color.White; log.SetBounds(30, 678, 940, 136);
        log.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
        Controls.Add(log);
        timer.Interval = 700; timer.Tick += delegate { RefreshStatus(); }; timer.Start();
        ApplyButtons(false, false, false, false);
        FormClosing += OnClosing;
    }

    void ConfigureSlots(int slots) {
        foreach (Control control in new List<Control>(GetFileControls())) control.Dispose();
        files.Controls.Clear(); fileLabels.Clear(); fileBars.Clear();
        for (int i = 0; i < Math.Max(1, Math.Min(16, slots)); i++) {
            var label = new Label { Text = "等待开始", ForeColor = Color.FromArgb(45, 56, 72) };
            label.SetBounds(6, i * 68, 895, 27); files.Controls.Add(label); fileLabels.Add(label);
            var bar = new ProgressBar(); bar.SetBounds(6, 33 + i * 68, 895, 14);
            files.Controls.Add(bar); fileBars.Add(bar);
        }
    }
    IEnumerable<Control> GetFileControls() { foreach (Control c in files.Controls) yield return c; }
    void ApplyButtons(bool running, bool paused, bool stopping, bool isMerging) {
        start.Enabled = !running;
        cancel.Enabled = running && !stopping;
        pause.Enabled = running && !paused && !stopping && !isMerging;
        resume.Enabled = running && paused && !stopping && !isMerging;
        config.Enabled = !running;
    }
    static string Quote(string s) { return "\"" + s + "\""; }
    Dictionary<string, object> ReadJson(string path) {
        return json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
    }
    static string Value(Dictionary<string, object> d, string key, string fallback) {
        return d.ContainsKey(key) && d[key] != null ? Convert.ToString(d[key], CultureInfo.InvariantCulture) : fallback;
    }
    static double Number(Dictionary<string, object> d, string key) {
        double result; return Double.TryParse(Value(d, key, "0"), NumberStyles.Any, CultureInfo.InvariantCulture, out result) ? result : 0;
    }
    bool Running { get { try { return worker != null && !worker.HasExited; } catch { return false; } } }
    void AddLog(string text) {
        if (closing || IsDisposed) return;
        if (InvokeRequired) { try { BeginInvoke(new Action<string>(AddLog), text); } catch {} return; }
        if (text.Length > 1200) text = text.Substring(0, 1200) + " …（完整记录见日志）";
        if (log.TextLength > 30000) log.Text = log.Text.Substring(log.TextLength - 18000);
        log.AppendText(DateTime.Now.ToString("HH:mm:ss") + "  " + text + Environment.NewLine);
    }
    string Python() {
        string portable = Path.Combine(root, "runtime", "python.exe");
        if (File.Exists(portable)) return portable;
        string custom = Path.Combine(root, "python_path.txt");
        if (File.Exists(custom)) {
            string selected = File.ReadAllText(custom).Trim();
            if (File.Exists(selected)) return selected;
            throw new FileNotFoundException("python_path.txt 指定的 Python 不存在", selected);
        }
        string profile = Environment.GetEnvironmentVariable("USERPROFILE") ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string bundled = Path.Combine(profile,
            ".cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe");
        if (File.Exists(bundled)) return bundled;
        string venv = Path.Combine(root, ".venv\\Scripts\\python.exe");
        if (File.Exists(venv)) return venv;
        throw new FileNotFoundException("找不到 Python。请在 python_path.txt 中填写 Python 的完整路径。");
    }
    void StartDownload() {
        if (Running) return;
        try {
            var cfg = ReadJson(Path.Combine(root, "config.json"));
            ConfigureSlots(cfg.ContainsKey("parallel_hours") ? Convert.ToInt32(cfg["parallel_hours"]) : 1);
            session = Path.Combine(root, ".gui_sessions", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(session);
            statusPath = Path.Combine(session, "status.json"); controlPath = Path.Combine(session, "control.json");
            dataFolder = null; previousBytes = 0; previousSample = DateTime.UtcNow;
            commandPaused = false; cancelling = false; merging = false; queueView.Items.Clear(); WriteCommand("run");
            string snapshot = Path.Combine(session, "config.json");
            File.Copy(Path.Combine(root, "config.json"), snapshot);
            worker = new Process();
            worker.StartInfo = new ProcessStartInfo {
                FileName = Python(), WorkingDirectory = root,
                Arguments = "-X utf8 -u " + Quote(Path.Combine(root, "download_queue.py")) +
                    " --config " + Quote(snapshot) +
                    " --status-file " + Quote(statusPath) + " --control-file " + Quote(controlPath),
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            worker.StartInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            DataReceivedEventHandler output = delegate(object sender, DataReceivedEventArgs e) {
                if (e.Data == null) return;
                lock (logLock) File.AppendAllText(Path.Combine(session, "worker.log"), e.Data + Environment.NewLine, Encoding.UTF8);
                if (!e.Data.StartsWith("{\"requested_hours\"")) AddLog(e.Data);
            };
            worker.OutputDataReceived += output; worker.ErrorDataReceived += output;
            worker.Start(); worker.BeginOutputReadLine(); worker.BeginErrorReadLine();
            // Reuse the existing stop script for this window's owned process.
            File.WriteAllText(Path.Combine(root, "background_process.json"), json.Serialize(new Dictionary<string, object> {
                { "process_id", worker.Id }, { "started_utc", worker.StartTime.ToUniversalTime().ToString("o") },
                { "script", Path.Combine(root, "download_queue.py") }, { "control", controlPath }
            }), Encoding.UTF8);
            summary.Text = "正在验证下载队列、读取断点记录…";
            ApplyButtons(true, false, false, false);
            AddLog("已读取 config.json，正在启动；重复任务会自动复用断点。");
        } catch (Exception e) {
            if (session != null) File.WriteAllText(Path.Combine(session, "startup_error.txt"), e.ToString(), Encoding.UTF8);
            AddLog("启动失败：" + e.Message); ApplyButtons(Running, false, false, false);
        }
    }
    void WriteCommand(string mode) {
        string temp = controlPath + ".tmp";
        File.WriteAllText(temp, json.Serialize(new Dictionary<string, object> { { "command", mode } }), new UTF8Encoding(false));
        for (int attempt = 0; attempt < 10; attempt++) {
            try {
                if (File.Exists(controlPath)) File.Replace(temp, controlPath, null); else File.Move(temp, controlPath);
                return;
            } catch (IOException) { if (attempt == 9) throw; Thread.Sleep(20); }
        }
    }
    void Send(string mode) {
        if (!Running || cancelling) return;
        if (mode == "run" && !commandPaused) return;
        try {
            WriteCommand(mode);
            if (mode == "stop") { cancelling = true; commandPaused = false; cancelRequested = DateTime.UtcNow; }
            else commandPaused = mode == "pause";
            ApplyButtons(true, commandPaused, cancelling, merging);
            AddLog(mode == "pause" ? "已暂停；点击“继续”恢复。" : mode == "run" ? "继续下载。" : "正在取消当前全部下载任务，保留已下载文件和断点。");
        } catch (Exception e) { AddLog("操作失败：" + e.Message); }
    }
    static string Duration(double seconds) {
        TimeSpan value = TimeSpan.FromSeconds(Math.Max(0, seconds));
        return ((int)value.TotalHours).ToString("00") + ":" + value.Minutes.ToString("00") + ":" + value.Seconds.ToString("00");
    }
    void ApplyStatus(Dictionary<string, object> state) {
        dataFolder = Value(state, "data_folder", dataFolder);
        bool queued = state.ContainsKey("queue_version");
        int done = (int)Number(state, queued ? "queue_completed_hours" : "completed_hours");
        int total = (int)Number(state, queued ? "queue_total_hours" : "requested_hours");
        overall.Value = total > 0 ? Math.Min(100, done * 100 / total) : 0;
        string phase = Value(state, "phase", "downloading");
        string runState = Value(state, "run_state", "running");
        merging = phase == "merging" && Running;
        string word = cancelling ? "正在取消…" : commandPaused ? "已暂停" : runState == "complete" ? "全部任务完成" : merging ? "正在合并校验" : phase == "height" ? "正在计算真实高度" : phase == "preparing" ? "正在准备任务" : "正在下载";
        summary.Text = String.Format("{0}    队列 {1}/{2}    总进度 {3}/{4} 小时", word, Math.Max(1,Number(state,"queue_task_index")), Math.Max(1,Number(state,"queue_task_count")), done, total);
        taskInfo.Text = String.Format("当前任务  {0}   {1} → {2}   {3}   ·   并行 {4} 小时", Value(state,"task_site",""),Value(state,"task_start","").Replace("T"," "),Value(state,"task_end","").Replace("T"," "),Value(state,"task_time_zone","")=="UTC"?"UTC":"北京时间",Number(state,"parallel_hours"));
        object eta = state.ContainsKey("eta_seconds") ? state["eta_seconds"] : null;
        timing.Text = "累计已用时 " + Duration(Number(state,"elapsed_active_seconds")) + "    ·    预计还需 " + (eta == null ? (merging ? "合并校验中" : phase=="height" ? "高度计算 "+Number(state,"height_percent").ToString("F0")+"%" : "估算中…") : "约 " + Duration(Convert.ToDouble(eta))) + (commandPaused ? "    （暂停不计时）" : "");
        int slots = (int)Number(state,"parallel_hours");
        if (slots > 0 && slots != fileBars.Count) ConfigureSlots(slots);
        var tasks = state.ContainsKey("queue_tasks") ? state["queue_tasks"] as IEnumerable : null;
        if (tasks != null) {
            queueView.BeginUpdate(); queueView.Items.Clear();
            foreach (object value in tasks) {
                var row = value as Dictionary<string,object>; if (row == null) continue;
                string label = Value(row,"state","pending");
                label = label=="complete"?"已完成":label=="running"?(commandPaused?"已暂停":merging?"合并中":phase=="height"?"真实高度":"处理中"):label=="failed"?"失败":label=="cancelled"?"已取消":"待处理";
                var item = new ListViewItem(new string[] {Value(row,"site",""),Value(row,"start","").Replace("T"," ")+" → "+Value(row,"end","").Replace("T"," "),Number(row,"completed_hours")+" / "+Number(row,"requested_hours"),label});
                if (label=="已完成") item.ForeColor=Color.FromArgb(25,130,90);
                queueView.Items.Add(item);
            }
            queueView.EndUpdate();
        }
        double bytes = Number(state, "network_bytes_this_run");
        double seconds = (DateTime.UtcNow - previousSample).TotalSeconds;
        if (seconds > 1) {
            rate.Text = String.Format("本次接收 {0:F1} MB   ·   {1:F2} MB/s   ·   缓存复用 {2:F1} MB", bytes / 1e6, Math.Max(0, bytes - previousBytes) / 1e6 / seconds, Number(state, "reused_cache_bytes_this_run") / 1e6);
            previousBytes = bytes; previousSample = DateTime.UtcNow;
        }
        var progress = state.ContainsKey("active_file_progress") ? state["active_file_progress"] as IEnumerable : null;
        int index = 0;
        if (progress != null) foreach (object entry in progress) {
            var row = entry as Dictionary<string, object>; if (row == null || index >= fileBars.Count) continue;
            int percent = Math.Max(0, Math.Min(100, (int)Number(row, "percent")));
            fileBars[index].Value = percent;
            fileLabels[index].Text = String.Format("{0}   {1:F1}%   数据块 {2}/{3}   本次接收 {4:F1} MB", Value(row, "file", ""), Number(row, "percent"), Number(row, "chunks_complete"), Number(row, "chunks_total"), Number(row, "network_bytes") / 1e6);
            index++;
        }
        for (; index < fileBars.Count; index++) { fileBars[index].Value = 0; fileLabels[index].Text = runState == "complete" ? "全部小时文件已下载完成" : "等待分配下载文件"; }
        if(phase=="height"&&fileBars.Count>0){
            fileBars[0].Value=Math.Max(0,Math.Min(100,(int)Number(state,"height_percent")));
            fileLabels[0].Text="真实高度文件 · 计算与校验 "+Number(state,"height_percent").ToString("F0")+"%";
            for(int i=1;i<fileBars.Count;i++)fileLabels[i].Text="本时段气象数据已经下载并合并";
        }
    }
    void RefreshStatus() {
        if (preview || closing) return;
        try { if (statusPath != null && File.Exists(statusPath)) ApplyStatus(ReadJson(statusPath)); } catch {}
        if (cancelling && Running && (DateTime.UtcNow - cancelRequested).TotalSeconds > 3) {
            try { worker.Kill(); } catch {}
        }
        bool running = Running;
        ApplyButtons(running, commandPaused, cancelling, merging);
        if (!running && cancelling) { summary.Text = "已取消当前全部任务 · 已下载文件和断点已保留"; rate.Text = "点击“开始”会重新读取配置；相同任务可接续。"; }
        else if (!running && worker != null && worker.ExitCode != 0) summary.Text = "队列已中止，请查看下方错误日志；断点仍然保留";
    }
    void OnClosing(object sender, FormClosingEventArgs e) {
        if (Running) {
            try { WriteCommand("stop"); } catch {}
            // Stop only our child; an unresponsive request can be terminated
            // without losing the already flushed .part resume data.
            if (!worker.WaitForExit(2500)) { try { worker.Kill(); worker.WaitForExit(1500); } catch {} }
        }
        closing = true; timer.Stop();
    }
    public void Preview(string path) {
        var state = new Dictionary<string, object> {
            { "run_state", "running" }, { "completed_hours", 52 }, { "requested_hours", 168 },
            { "queue_version",2 }, { "queue_task_index",1 }, { "queue_task_count",2 },
            { "queue_completed_hours",52 }, { "queue_total_hours",936 }, { "phase","downloading" },
            { "task_site","station_A" }, { "task_start","2019-09-23T00:00:00" }, { "task_end","2019-09-29T23:00:00" },
            { "task_time_zone","beijing" }, { "elapsed_active_seconds",1845 }, { "eta_seconds",12600 },
            { "queue_tasks",new object[] {
                new Dictionary<string,object>{{"site","station_A"},{"start","2019-09-23"},{"end","2019-09-29"},{"requested_hours",168},{"completed_hours",52},{"state","running"}},
                new Dictionary<string,object>{{"site","station_B"},{"start","2020-01-01"},{"end","2020-02-01"},{"requested_hours",768},{"completed_hours",0},{"state","pending"}}
            } },
            { "parallel_hours", 3 }, { "network_bytes_this_run", 140000000 },
            { "active_file_progress", new object[] {
                new Dictionary<string, object>{{"file","20160515_23.nc"},{"percent",67.6},{"chunks_complete",23},{"chunks_total",34},{"network_bytes",138000000}},
                new Dictionary<string, object>{{"file","20160516_00.nc"},{"percent",44.1},{"chunks_complete",15},{"chunks_total",34},{"network_bytes",90000000}},
                new Dictionary<string, object>{{"file","20160516_01.nc"},{"percent",20.6},{"chunks_complete",7},{"chunks_total",34},{"network_bytes",42000000}}
            }}
        };
        ApplyStatus(state);
        ApplyButtons(true, false, false, false);
        AddLog("文件进度按已完成的数据块计算。取消会结束当前全部任务，保留已下载文件和断点。");
        ShowInTaskbar = false; Opacity = 0; Show(); Application.DoEvents();
        using (var bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(path); }
        Hide();
    }
    public void SelfTest(string path) {
        int buttons = 0;
        foreach (Control c in Controls) if (c is Button) buttons++;
        if (buttons != 5) throw new Exception("Expected exactly five buttons");
        ApplyButtons(false, false, false, false);
        if (!start.Enabled || cancel.Enabled || pause.Enabled || resume.Enabled || !config.Enabled) throw new Exception("Idle state failed");
        ApplyButtons(true, false, false, false);
        if (start.Enabled || !cancel.Enabled || !pause.Enabled || resume.Enabled || config.Enabled) throw new Exception("Running state failed");
        ApplyButtons(true, true, false, false);
        if (pause.Enabled || !resume.Enabled) throw new Exception("Paused state failed");
        ApplyButtons(true, false, true, false);
        if (start.Enabled || cancel.Enabled || pause.Enabled || resume.Enabled) throw new Exception("Cancel state failed");
        ApplyButtons(true, false, false, true);
        if (pause.Enabled || resume.Enabled || !cancel.Enabled) throw new Exception("Merge state failed");
        File.WriteAllText(path, json.Serialize(new Dictionary<string, object> {
            { "ui_test", "PASS" }, { "buttons", 5 }, { "states", "idle/running/paused/cancelling/merging" },
            { "python", Python() }, { "auto_start", false }
        }), Encoding.UTF8);
    }
    [STAThread]
    static int Main(string[] args) {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        if (args.Length == 2 && args[0] == "--preview") {
            using (var form = new DownloaderWindow(true)) form.Preview(args[1]); return 0;
        }
        if (args.Length == 2 && args[0] == "--self-test") {
            using (var form = new DownloaderWindow(true)) form.SelfTest(args[1]); return 0;
        }
        if (args.Length == 2 && args[0] == "--config-preview") {
            using(var form=new ConfigurationWindow(AppDomain.CurrentDomain.BaseDirectory))form.Preview(args[1]);return 0;
        }
        if (args.Length == 2 && args[0] == "--config-self-test") {
            using(var form=new ConfigurationWindow(AppDomain.CurrentDomain.BaseDirectory))form.SelfTest(args[1]);return 0;
        }
        bool created;
        using (var mutex = new Mutex(true, "Local\\ERA5Downloader_" + AppDomain.CurrentDomain.BaseDirectory.GetHashCode(), out created)) {
            if (!created) {
                IntPtr existing = FindWindow(null, "ERA5-reanalysis-complete下载器v2.0");
                if (existing != IntPtr.Zero) {
                    ShowWindowAsync(existing, 9); // Restore a minimized window.
                    SetForegroundWindow(existing);
                    return 0;
                }
                // A stale instance on another desktop must not prevent the
                // user from opening a visible window on their own desktop.
            }
            Application.Run(new DownloaderWindow(false));
        }
        return 0;
    }
}
