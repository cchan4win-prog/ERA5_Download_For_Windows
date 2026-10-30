using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Windows.Forms;
using System.Web.Script.Serialization;

sealed class ConfigurationWindow : Form {
    readonly string root;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength=8000000 };
    Dictionary<string,object> document;
    readonly List<Dictionary<string,object>> sites = new List<Dictionary<string,object>>();
    readonly TreeView tree = new TreeView();
    readonly TextBox siteName=new TextBox(), latitude=new TextBox(), longitude=new TextBox();
    readonly TextBox north=new TextBox(), south=new TextBox(), west=new TextBox(), east=new TextBox(), output=new TextBox();
    readonly RadioButton point=new RadioButton{Text="站点最近网格"}, area=new RadioButton{Text="区域下载"};
    readonly CheckBox enabled=new CheckBox{Text="启用站点"}, heights=new CheckBox{Text="生成真实高度文件（自动补齐高度所需参量）"};
    readonly DateTimePicker start=new DateTimePicker(), end=new DateTimePicker();
    readonly ComboBox zone=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList};
    readonly NumericUpDown parallel=new NumericUpDown{Minimum=1,Maximum=16}, workers=new NumericUpDown{Minimum=1,Maximum=64};
    readonly List<CheckBox> hours=new List<CheckBox>(), variables=new List<CheckBox>();
    readonly GroupBox geography=new GroupBox(), dates=new GroupBox(), content=new GroupBox(), identity=new GroupBox();
    readonly Label error=new Label();
    readonly string[] aliases={"u","v","t","q","w","o3","d","vo","cc","clwc","ciwc","crwc","cswc"};
    int selectedSite=-1, selectedPeriod=0;
    bool loading;
    sealed class Selection { public int Site,Period; public Selection(int s,int p){Site=s;Period=p;} }

    public ConfigurationWindow(string directory) {
        root=directory;
        Text="配置参数 · ERA5 下载队列";
        ClientSize=new Size(1190,780); MinimumSize=new Size(1206,819);
        Font=new Font("Microsoft YaHei UI",10F); BackColor=Color.FromArgb(241,245,250);
        StartPosition=FormStartPosition.CenterParent;
        Label title=LabelAt(this,"下载参数",22,18,500,34);
        title.Font=new Font(Font.FontFamily,19F,FontStyle.Bold);title.ForeColor=Color.FromArgb(24,42,69);
        LabelAt(this,"左侧选择站点或时间段；保存后，点击主窗口“开始”执行。",22,53,1000,24);
        tree.SetBounds(20,89,230,554); tree.HideSelection=false; Controls.Add(tree);
        Button add=ButtonAt(this,"＋ 添加",20,656,108,36);
        Button remove=ButtonAt(this,"删除所选",140,656,110,36);
        ContextMenuStrip menu=new ContextMenuStrip();
        menu.Items.Add("新站点",null,delegate { AddSite(); });
        menu.Items.Add("当前站点的新时间段",null,delegate { AddPeriod(); });
        add.Click+=delegate {menu.Show(add,new Point(0,add.Height));};
        remove.Click+=delegate {DeleteSelected();};
        error.SetBounds(20,707,235,55);error.ForeColor=Color.FromArgb(165,55,55);Controls.Add(error);

        SetupGroup(identity,"站点",275,89,890,69);
        LabelAt(identity,"名称",14,28,45,25);siteName.SetBounds(65,25,330,28);identity.Controls.Add(siteName);
        enabled.SetBounds(411,28,100,25);identity.Controls.Add(enabled);
        point.SetBounds(535,26,143,28);area.SetBounds(700,26,140,28);identity.Controls.Add(point);identity.Controls.Add(area);
        point.CheckedChanged+=delegate {UpdateMode();};
        area.CheckedChanged+=delegate {UpdateMode();};

        SetupGroup(geography,"地理位置 · 单位：度",275,174,470,348);
        LabelAt(geography,"北界（纬度）",155,26,155,24);Place(geography,north,151,52,155);
        LabelAt(geography,"西界（经度）",17,133,130,24);Place(geography,west,17,159,115);
        LabelAt(geography,"东界（经度）",329,133,130,24);Place(geography,east,329,159,120);
        LabelAt(geography,"站点纬度",167,112,135,24);Place(geography,latitude,151,139,155);
        LabelAt(geography,"站点经度",167,180,135,24);Place(geography,longitude,151,207,155);
        LabelAt(geography,"南界（纬度）",155,263,155,24);Place(geography,south,151,289,155);
        LabelAt(geography,"站点模式使用中心坐标；区域模式使用四周边界。",17,321,435,22).Font=new Font(Font.FontFamily,8.5F);

        SetupGroup(dates,"时间与并行设置 · 当前选定时间段",762,174,403,432);
        LabelAt(dates,"开始",15,31,48,24);PlaceDate(start,dates,69,26);
        LabelAt(dates,"结束",15,73,48,24);PlaceDate(end,dates,69,68);
        LabelAt(dates,"时区",15,115,48,24);zone.Items.AddRange(new object[]{"北京时间（UTC+8）","UTC"});
        zone.SetBounds(69,109,315,30);dates.Controls.Add(zone);
        LabelAt(dates,"下载小时（按上面选择的时区）",15,153,360,24);
        for(int h=0;h<24;h++){
            CheckBox check=new CheckBox{Text=h.ToString("00"),Tag=h};
            check.SetBounds(16+(h%6)*61,184+(h/6)*29,59,25);dates.Controls.Add(check);hours.Add(check);
        }
        Button all=ButtonAt(dates,"全选",16,310,82,30);all.Click+=delegate {foreach(CheckBox h in hours)h.Checked=true;};
        Button night=ButtonAt(dates,"20点～次日8点",110,310,166,30);night.Click+=delegate {for(int h=0;h<24;h++)hours[h].Checked=h>=20||h<=8;};
        LabelAt(dates,"并行小时文件数",16,363,160,25);parallel.SetBounds(193,360,90,29);dates.Controls.Add(parallel);
        LabelAt(dates,"1～16 个，网络并发总量在右下方设置",16,399,370,22).Font=new Font(Font.FontFamily,8.5F);

        SetupGroup(content,"参量与高度输出",275,539,470,165);
        for(int i=0;i<aliases.Length;i++){
            CheckBox check=new CheckBox{Text=aliases[i],Tag=aliases[i]};
            check.SetBounds(16+(i%7)*63,28+(i/7)*31,62,25);content.Controls.Add(check);variables.Add(check);
        }
        heights.SetBounds(16,103,440,27);content.Controls.Add(heights);
        LabelAt(content,"真实高度使用完整137层T/Q、地面气压和地表位势。",16,136,442,22).Font=new Font(Font.FontFamily,8.5F);
        GroupBox common=new GroupBox();SetupGroup(common,"全局网络与输出位置",762,622,403,83);
        LabelAt(common,"网络并发",12,25,85,24);workers.SetBounds(101,21,73,28);common.Controls.Add(workers);
        LabelAt(common,"输出目录",12,56,85,24);Place(common,output,101,51,283);
        Button save=ButtonAt(this,"保存配置",930,731,110,35);
        Button cancel=ButtonAt(this,"取消",1054,731,110,35);
        save.BackColor=Color.FromArgb(40,100,200);save.ForeColor=Color.White;
        save.Click+=delegate {Save();};cancel.Click+=delegate {DialogResult=DialogResult.Cancel;Close();};
        tree.BeforeSelect+=delegate {if(!loading)Commit();};
        tree.AfterSelect+=delegate(object sender,TreeViewEventArgs e){if(!loading){Selection pick=e.Node.Tag as Selection;if(pick!=null)LoadSelection(pick.Site,pick.Period);}};
        LoadDocument();
    }
    static Label LabelAt(Control parent,string text,int x,int y,int w,int h){Label l=new Label{Text=text};l.SetBounds(x,y,w,h);parent.Controls.Add(l);return l;}
    static Button ButtonAt(Control parent,string text,int x,int y,int w,int h){Button b=new Button{Text=text,FlatStyle=FlatStyle.Flat,BackColor=Color.White};b.FlatAppearance.BorderColor=Color.FromArgb(204,215,230);b.SetBounds(x,y,w,h);parent.Controls.Add(b);return b;}
    static void Place(Control parent,TextBox box,int x,int y,int w){box.SetBounds(x,y,w,28);parent.Controls.Add(box);}
    static void PlaceDate(DateTimePicker p,Control parent,int x,int y){p.Format=DateTimePickerFormat.Custom;p.CustomFormat="yyyy-MM-dd HH:00";p.SetBounds(x,y,315,29);parent.Controls.Add(p);}
    void SetupGroup(GroupBox group,string title,int x,int y,int w,int h){group.Text=title;group.BackColor=Color.White;group.SetBounds(x,y,w,h);Controls.Add(group);}
    static string S(Dictionary<string,object> d,string k,string fallback){return d.ContainsKey(k)&&d[k]!=null?Convert.ToString(d[k],CultureInfo.InvariantCulture):fallback;}
    static bool Bool(Dictionary<string,object> d,string k,bool fallback){return d.ContainsKey(k)&&d[k]!=null?Convert.ToBoolean(d[k]):fallback;}
    static List<Dictionary<string,object>> Objects(object value){var result=new List<Dictionary<string,object>>();IEnumerable all=value as IEnumerable;if(all!=null)foreach(object x in all){var d=x as Dictionary<string,object>;if(d!=null)result.Add(d);}return result;}
    static List<int> Integers(object value){var result=new List<int>();IEnumerable all=value as IEnumerable;if(all!=null)foreach(object x in all){try{result.Add(Convert.ToInt32(x));}catch{}}return result;}
    static List<string> Strings(object value){var result=new List<string>();IEnumerable all=value as IEnumerable;if(all!=null)foreach(object x in all)result.Add(Convert.ToString(x));return result;}
    static DateTime Date(string value,bool finish){DateTime t;if(!DateTime.TryParse(value,CultureInfo.InvariantCulture,DateTimeStyles.None,out t))t=new DateTime(2020,1,1);if(value.Length==10&&finish)t=t.Date.AddHours(23);return DateTime.SpecifyKind(t,DateTimeKind.Unspecified);}
    List<Dictionary<string,object>> Periods(Dictionary<string,object> site){return Objects(site["periods"]);}
    Dictionary<string,object> Effective(int s,int p){var d=new Dictionary<string,object>(document);foreach(var kv in sites[s])d[kv.Key]=kv.Value;foreach(var kv in Periods(sites[s])[p])d[kv.Key]=kv.Value;return d;}
    void LoadDocument(){
        string path=Path.Combine(root,"config.json");
        document=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path, System.Text.Encoding.UTF8));
        bool legacy=!document.ContainsKey("tasks");
        if(!legacy)sites.AddRange(Objects(document["tasks"]));
        else{
            var site=new Dictionary<string,object>(document);
            site["periods"]=new List<Dictionary<string,object>>{new Dictionary<string,object>{{"start",S(site,"start","2020-01-01")},{"end",S(site,"end","2020-01-01")}}};
            // Legacy Asia/Shanghai used UTC hours; preserve the actual hours.
            if(S(document,"time_zone","UTC")=="Asia/Shanghai"&&!document.ContainsKey("hours_time_zone")){
                var converted=Integers(document.ContainsKey("hours")?document["hours"]:new int[0]);
                for(int i=0;i<converted.Count;i++)converted[i]=(converted[i]+8)%24;
                site["hours"]=converted;site["time_zone"]="beijing";
            }
            sites.Add(site);
        }
        foreach(var site in sites){
            var periods=site.ContainsKey("periods")?Objects(site["periods"]):site.ContainsKey("time_ranges")?Objects(site["time_ranges"]):new List<Dictionary<string,object>>();
            if(periods.Count==0)periods.Add(new Dictionary<string,object>{{"start",S(site,"start",S(document,"start","2020-01-01"))},{"end",S(site,"end",S(document,"end","2020-01-01"))}});
            site["periods"]=periods;
        }
        // Make inherited hour/timezone settings explicit for every period,
        // including periods that the user does not select before saving.
        for(int si=0;si<sites.Count;si++){
            var ps=Periods(sites[si]);
            for(int pi=0;pi<ps.Count;pi++){
                var effective=Effective(si,pi);
                bool utc=S(effective,"time_zone","beijing").Equals("UTC",StringComparison.OrdinalIgnoreCase);
                string hz=S(effective,"hours_time_zone",utc?"UTC":"beijing");
                var hs=effective.ContainsKey("hours")?Integers(effective["hours"]):new List<int>();
                if(!effective.ContainsKey("hours"))for(int h=0;h<24;h++)hs.Add(h);
                bool hUtc=hz.Equals("UTC",StringComparison.OrdinalIgnoreCase);
                if(hUtc!=utc)for(int h=0;h<hs.Count;h++)hs[h]=(hs[h]+(utc?16:8))%24;
                ps[pi]["time_zone"]=utc?"UTC":"beijing";ps[pi]["hours"]=hs;
                ps[pi]["parallel_hours"]=effective.ContainsKey("parallel_hours")?effective["parallel_hours"]:3;
                ps[pi]["variables"]=effective.ContainsKey("variables")?effective["variables"]:new string[]{"u","v","t"};
                ps[pi]["generate_real_height"]=Bool(effective,"generate_real_height",Bool(effective,"include_height_inputs",true));
                foreach(string key in new string[]{"latitude","longitude","north","south","west","east","spatial_mode"})if(effective.ContainsKey(key))ps[pi][key]=effective[key];
                ps[pi].Remove("hours_time_zone");
            }
            sites[si]["periods"]=ps;
        }
        foreach(var site in sites)site.Remove("hours_time_zone");document.Remove("hours_time_zone");
        workers.Value=Math.Max(1,Math.Min(64,Decimal.Parse(S(document,"workers","24"),CultureInfo.InvariantCulture)));
        output.Text=S(document,"output_dir","data");
        Rebuild(0,0);
    }
    void Rebuild(int wantedSite,int wantedPeriod){
        loading=true;tree.Nodes.Clear();
        for(int s=0;s<sites.Count;s++){
            var parent=new TreeNode(S(sites[s],"site","未命名站点")){Tag=new Selection(s,0)};
            var ps=Periods(sites[s]);
            for(int p=0;p<ps.Count;p++)parent.Nodes.Add(new TreeNode(S(ps[p],"start","开始")+" → "+S(ps[p],"end","结束")){Tag=new Selection(s,p)});
            tree.Nodes.Add(parent);parent.Expand();
        }
        if(sites.Count>0){wantedSite=Math.Max(0,Math.Min(sites.Count-1,wantedSite));wantedPeriod=Math.Max(0,Math.Min(Periods(sites[wantedSite]).Count-1,wantedPeriod));tree.SelectedNode=tree.Nodes[wantedSite].Nodes[wantedPeriod];}
        loading=false;
        if(sites.Count>0)LoadSelection(wantedSite,wantedPeriod);
        else{selectedSite=-1;identity.Enabled=geography.Enabled=dates.Enabled=content.Enabled=false;}
    }
    void LoadSelection(int s,int p){
        loading=true;selectedSite=s;selectedPeriod=p;
        identity.Enabled=geography.Enabled=dates.Enabled=content.Enabled=true;
        var d=Effective(s,p);var site=sites[s];
        siteName.Text=S(site,"site","");enabled.Checked=Bool(site,"enabled",true);
        latitude.Text=S(d,"latitude","");longitude.Text=S(d,"longitude","");north.Text=S(d,"north","");south.Text=S(d,"south","");west.Text=S(d,"west","");east.Text=S(d,"east","");
        point.Checked=S(d,"spatial_mode","point")=="point";area.Checked=!point.Checked;
        start.Value=Date(S(d,"start","2020-01-01"),false);end.Value=Date(S(d,"end","2020-01-01"),true);
        string tz=S(d,"time_zone","beijing");zone.SelectedIndex=tz.Equals("UTC",StringComparison.OrdinalIgnoreCase)?1:0;
        List<int> selected=d.ContainsKey("hours")?Integers(d["hours"]):new List<int>();if(!d.ContainsKey("hours"))for(int h=0;h<24;h++)selected.Add(h);
        string hourZone=S(d,"hours_time_zone",tz);bool hourUtc=hourZone=="UTC",dateUtc=zone.SelectedIndex==1;
        if(hourUtc!=dateUtc){for(int i=0;i<selected.Count;i++)selected[i]=(selected[i]+(dateUtc?16:8))%24;}
        for(int h=0;h<24;h++)hours[h].Checked=selected.Contains(h);
        parallel.Value=Math.Max(1,Math.Min(16,Decimal.Parse(S(d,"parallel_hours","3"),CultureInfo.InvariantCulture)));
        var vars=d.ContainsKey("variables")?Strings(d["variables"]):new List<string>{"u","v","t"};
        foreach(CheckBox check in variables)check.Checked=vars.Contains((string)check.Tag);
        heights.Checked=Bool(d,"generate_real_height",Bool(d,"include_height_inputs",true));
        loading=false;UpdateMode();
    }
    static object Coordinate(TextBox box){double n;if(Double.TryParse(box.Text.Trim(),NumberStyles.Float,CultureInfo.InvariantCulture,out n)&&!Double.IsNaN(n)&&!Double.IsInfinity(n))return n;return null;}
    void Commit(){
        if(loading||selectedSite<0||selectedSite>=sites.Count)return;
        var site=sites[selectedSite];var ps=Periods(site);if(selectedPeriod>=ps.Count)return;
        var period=ps[selectedPeriod];site["site"]=siteName.Text.Trim();site["enabled"]=enabled.Checked;
        site["spatial_mode"]=point.Checked?"point":"bbox";
        site["latitude"]=Coordinate(latitude);site["longitude"]=Coordinate(longitude);
        site["north"]=Coordinate(north);site["south"]=Coordinate(south);site["west"]=Coordinate(west);site["east"]=Coordinate(east);
        foreach(string key in new string[]{"latitude","longitude","north","south","west","east","spatial_mode"})period[key]=site[key];
        period["start"]=start.Value.ToString("yyyy-MM-ddTHH:00:00",CultureInfo.InvariantCulture);period["end"]=end.Value.ToString("yyyy-MM-ddTHH:00:00",CultureInfo.InvariantCulture);
        period["time_zone"]=zone.SelectedIndex==1?"UTC":"beijing";period["parallel_hours"]=(int)parallel.Value;
        var selected=new List<int>();for(int h=0;h<24;h++)if(hours[h].Checked)selected.Add(h);period["hours"]=selected;
        var vars=new List<string>();foreach(CheckBox check in variables)if(check.Checked)vars.Add((string)check.Tag);period["variables"]=vars;
        period["generate_real_height"]=heights.Checked;
        if(heights.Checked){period["include_height_inputs"]=true;period["levels"]="all";}
        // The visual editor's one timezone applies to dates and hour filtering.
        period.Remove("hours_time_zone");site.Remove("hours_time_zone");
        site["periods"]=ps;
    }
    void UpdateMode(){north.Enabled=south.Enabled=west.Enabled=east.Enabled=area.Checked;}
    void AddSite(){Commit();var site=new Dictionary<string,object>{{"site","新站点"+(sites.Count+1)},{"latitude",null},{"longitude",null},{"spatial_mode","point"},{"periods",new List<Dictionary<string,object>>{new Dictionary<string,object>{{"start","2020-01-01"},{"end","2020-01-01"}}}}};sites.Add(site);Rebuild(sites.Count-1,0);}
    void AddPeriod(){Commit();if(selectedSite<0){AddSite();return;}var ps=Periods(sites[selectedSite]);var copy=json.Deserialize<Dictionary<string,object>>(json.Serialize(ps[Math.Min(selectedPeriod,ps.Count-1)]));ps.Add(copy);sites[selectedSite]["periods"]=ps;Rebuild(selectedSite,ps.Count-1);}
    void DeleteSelected(){if(selectedSite<0)return;var ps=Periods(sites[selectedSite]);bool siteSelected=tree.SelectedNode!=null&&tree.SelectedNode.Parent==null;if(siteSelected||ps.Count==1){sites.RemoveAt(selectedSite);Rebuild(Math.Max(0,selectedSite-1),0);}else{ps.RemoveAt(selectedPeriod);sites[selectedSite]["periods"]=ps;Rebuild(selectedSite,Math.Max(0,selectedPeriod-1));}}
    double Number(Dictionary<string,object> d,string key,double min,double max){double v;if(!d.ContainsKey(key)||d[key]==null||!Double.TryParse(S(d,key,""),NumberStyles.Float,CultureInfo.InvariantCulture,out v)||Double.IsNaN(v)||Double.IsInfinity(v)||v<min||v>max)throw new Exception("请填写有效的 "+key+"（"+min+"～"+max+"）");return v;}
    void ValidateAll(){
        int count=0;
        for(int s=0;s<sites.Count;s++){
            if(!Bool(sites[s],"enabled",true))continue;
            if(String.IsNullOrWhiteSpace(S(sites[s],"site","")))throw new Exception("站点名称不能为空");
            var ps=Periods(sites[s]);
            for(int p=0;p<ps.Count;p++){
                var d=Effective(s,p);
                if(S(d,"spatial_mode","point")=="point"){Number(d,"latitude",-90,90);Number(d,"longitude",-180,360);}
                else{double n=Number(d,"north",-90,90),so=Number(d,"south",-90,90);Number(d,"west",-180,360);Number(d,"east",-180,360);if(n<so)throw new Exception("北界必须大于或等于南界");}
                DateTime a,b;
                if(!DateTime.TryParse(S(d,"start",""),CultureInfo.InvariantCulture,DateTimeStyles.None,out a)||!DateTime.TryParse(S(d,"end",""),CultureInfo.InvariantCulture,DateTimeStyles.None,out b)||a>b)throw new Exception("开始时间必须早于或等于结束时间");
                var hs=d.ContainsKey("hours")?Integers(d["hours"]):new List<int>();if(hs.Count==0)throw new Exception("每段时间至少勾选一个下载小时");foreach(int h in hs)if(h<0||h>23)throw new Exception("下载小时必须为0～23");
                if(!d.ContainsKey("variables")||Strings(d["variables"]).Count==0)throw new Exception("请至少勾选一个气象参量");
                if(Bool(d,"generate_real_height",false)){ps[p]["include_height_inputs"]=true;ps[p]["levels"]="all";}
                Number(d,"parallel_hours",1,16);count++;
            }
        }
        if(count==0)throw new Exception("请至少添加并启用一个站点和时间段");
    }
    Dictionary<string,object> PrepareDocument(){
            Commit();document["workers"]=(int)workers.Value;document["output_dir"]=output.Text.Trim();
            if(String.IsNullOrWhiteSpace(output.Text))throw new Exception("输出目录不能为空");
            document["schema_version"]=2;document.Remove("hours_time_zone");document["tasks"]=sites;
            ValidateAll();
            return document;
    }
    void Save(){
        try{
            PrepareDocument();
            string path=Path.Combine(root,"config.json"),temp=path+".editing.tmp";
            File.WriteAllText(temp,json.Serialize(document),new System.Text.UTF8Encoding(false));
            if(File.Exists(path))File.Copy(path,path+".bak",true);
            if(File.Exists(path))File.Replace(temp,path,null);else File.Move(temp,path);
            DialogResult=DialogResult.OK;Close();
        }catch(Exception e){error.Text=e.Message;MessageBox.Show(this,e.Message,"请检查配置",MessageBoxButtons.OK,MessageBoxIcon.Information);}
    }
    public void Preview(string path){ShowInTaskbar=false;Opacity=0;Show();Application.DoEvents();using(var b=new Bitmap(Width,Height)){DrawToBitmap(b,new Rectangle(0,0,Width,Height));b.Save(path);}Hide();}
    public void SelfTest(string path){
        byte[] original=File.ReadAllBytes(Path.Combine(root,"config.json"));
        if(sites.Count==0)throw new Exception("Configuration import failed");
        int before=sites.Count;AddSite();if(sites.Count!=before+1)throw new Exception("Add site failed");
        latitude.Text="41.25";longitude.Text="100.5";siteName.Text="UI_test";
        start.Value=new DateTime(2020,1,1,20,0,0);end.Value=new DateTime(2020,1,2,8,0,0);zone.SelectedIndex=0;
        for(int h=0;h<24;h++)hours[h].Checked=h>=20||h<=8;
        AddPeriod();if(Periods(sites[selectedSite]).Count!=2)throw new Exception("Add period failed");
        Commit();var d=Effective(selectedSite,1);if(S(d,"time_zone","")!="beijing"||Integers(d["hours"]).Count!=13)throw new Exception("Hours/timezone edit failed");
        var export=PrepareDocument();
        File.WriteAllText(path+".config.json",json.Serialize(export),System.Text.Encoding.UTF8);
        if(Convert.ToBase64String(original)!=Convert.ToBase64String(File.ReadAllBytes(Path.Combine(root,"config.json"))))throw new Exception("Original config was changed during self test");
        File.WriteAllText(path,json.Serialize(new Dictionary<string,object>{{"config_ui","PASS"},{"add_site",true},{"add_period",true},{"compass_coordinates",true},{"timezone_hours",true},{"roundtrip_export",path+".config.json"},{"original_file_unchanged",true}}),System.Text.Encoding.UTF8);
    }
}
