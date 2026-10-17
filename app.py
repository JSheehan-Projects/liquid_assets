import os
import streamlit as st
import pandas as pd
import re
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import tempfile
import plotly.express as px

def clean_name(name):
    """Replaces WhatsApp's hidden narrow no-break spaces with standard spaces."""
    if not isinstance(name, str):
        return name
    return name.replace('\u202f', ' ').replace('\xa0', ' ').strip()

@st.cache_data
def parse_whatsapp_adds(file_content, admin_name="Jack"):
    """Parses WhatsApp txt export to build the tree, list of names, and track active members."""
    relationships = []
    seen_members = set([admin_name])
    all_chat_names = set([admin_name])
    active_members = set([admin_name])
    
    manual_overrides = [
        {"Adder": "Niall Finn", "Added": "~ Caoimhe"},
        {"Adder": "Aoife", "Added": "~ James Curneen"},
        {"Adder": "Andrew", "Added": "Alan Parker"},
        {"Adder": "Aoife", "Added": "~ Keith Warren"},
        {"Adder": "~ Caoimhe", "Added": "~ Anna Murphy"}
    ]
    
    for override in manual_overrides:
        adder = clean_name(override["Adder"])
        added = clean_name(override["Added"])
        relationships.append({"Adder": adder, "Added": added})
        seen_members.add(added)
        all_chat_names.add(adder)
        all_chat_names.add(added)
        active_members.add(added)
    
    pattern_explicit = re.compile(r"^\d{2}/\d{2}/\d{4},\s\d{2}:\d{2}\s-\s(.*?)\sadded\s(.*)$")
    pattern_implicit = re.compile(r"^\d{2}/\d{2}/\d{4},\s\d{2}:\d{2}\s-\s(.*?) were added$")
    pattern_joined = re.compile(r"^\d{2}/\d{2}/\d{4},\s\d{2}:\d{2}\s-\s(.*?) joined using a group link")
    pattern_left = re.compile(r"^\d{2}/\d{2}/\d{4},\s\d{2}:\d{2}\s-\s(.*?)\sleft$")
    pattern_removed = re.compile(r"^\d{2}/\d{2}/\d{4},\s\d{2}:\d{2}\s-\s(.*?)\sremoved\s(.*)$")
    
    for line in file_content.splitlines():
        match_explicit = pattern_explicit.match(line)
        match_implicit = pattern_implicit.match(line)
        match_joined = pattern_joined.match(line)
        match_left = pattern_left.match(line)
        match_removed = pattern_removed.match(line)
        
        if match_explicit:
            targets = re.split(r",\s*|\s+and\s+", match_explicit.group(2).strip())
            for t in targets: active_members.add(clean_name(t.strip()))
        elif match_implicit:
            targets = re.split(r",\s*|\s+and\s+", match_implicit.group(1).strip())
            for t in targets: active_members.add(clean_name(t.strip()))
        elif match_joined:
            active_members.add(clean_name(match_joined.group(1).strip()))
        elif match_left:
            leaver = clean_name(match_left.group(1).strip())
            if leaver in active_members: active_members.remove(leaver)
        elif match_removed:
            removed_person = clean_name(match_removed.group(2).strip())
            if removed_person in active_members: active_members.remove(removed_person)

        actor = None
        targets_raw = None
        
        if match_explicit:
            actor = match_explicit.group(1).strip()
            targets_raw = match_explicit.group(2).strip()
            if actor == "You":
                actor = admin_name
        elif match_implicit:
            actor = admin_name
            targets_raw = match_implicit.group(1).strip()
            
        if actor and targets_raw:
            actor = clean_name(actor)
            targets = re.split(r",\s*|\s+and\s+", targets_raw)
            
            for target in targets:
                target = clean_name(target)
                if target and target not in seen_members:
                    relationships.append({"Adder": actor, "Added": target})
                    seen_members.add(target)
                    all_chat_names.add(target)
                    all_chat_names.add(actor)
                    
    sorted_names = sorted(list(all_chat_names))
    return pd.DataFrame(relationships), sorted_names, active_members

def calculate_invitation_points(G, active_members):
    """Calculates points based on network depth, counting only active group members."""
    points_data = []
    
    for node in G.nodes():
        descendant_paths = nx.single_source_shortest_path_length(G, node)
        member_data = {"Member": node, "Points": 0.0}
        total_points = 0.0
        
        for descendant, distance in descendant_paths.items():
            if distance > 0 and descendant in active_members:
                total_points += (0.5 ** (distance - 1))
                degree_key = f"Level {distance}"
                member_data[degree_key] = member_data.get(degree_key, 0) + 1
                
        if total_points > 0:
            member_data["Points"] = total_points
            points_data.append(member_data)
            
    df_points = pd.DataFrame(points_data)
    
    if not df_points.empty:
        df_points = df_points.fillna(0)
        df_points = df_points.sort_values(by="Points", ascending=False).reset_index(drop=True)
        df_points["Points"] = df_points["Points"].round(2)
        df_points.index = df_points.index + 1
        df_points.index.name = "Rank"
        
        level_cols = [col for col in df_points.columns if col.startswith("Level")]
        level_cols = sorted(level_cols, key=lambda x: int(x.split(" ")[1]))
        
        for col in level_cols:
            df_points[col] = df_points[col].astype(int)
            
        df_points = df_points[["Member", "Points"] + level_cols]
        
    return df_points

def get_lineage(G, target):
    """Traces the path from the root down to the target member."""
    if target not in G.nodes():
        return []
    
    path = [target]
    current = target
    while True:
        preds = list(G.predecessors(current))
        if not preds:
            break
        current = preds[0]
        path.append(current)
    return path[::-1]

def prep_plotly_data(df, search_member=None, lineage=[]):
    """Prepares the dataframe for Plotly and applies colour highlighting if searched."""
    roots = set(df['Adder']) - set(df['Added'])
    root_rows = pd.DataFrame([{"Adder": "", "Added": root} for root in roots])
    plotly_df = pd.concat([root_rows, df], ignore_index=True)
    
    if search_member and search_member != "-- None --":
        def get_colour(node):
            if node == search_member:
                return "Target"
            elif node in lineage:
                return "Inviter Chain"
            else:
                return "Other"
        plotly_df['Highlight'] = plotly_df['Added'].apply(get_colour)
    else:
        plotly_df['Highlight'] = "Standard"
        
    return plotly_df

def render_pyvis_graph(G, layout_type):
    """Renders a PyVis network graph based on the selected layout style."""
    net = Network(height="700px", width="100%", directed=True, bgcolor="#222222", font_color="white")
    net.from_nx(G)
    
    if layout_type == "Top-Down Hierarchical":
        net.set_options("""
        var options = {
          "layout": { "hierarchical": { "enabled": true, "direction": "UD", "sortMethod": "directed" } },
          "physics": { "hierarchicalRepulsion": { "nodeDistance": 150 }, "solver": "hierarchicalRepulsion" }
        }
        """)
    else:
        net.set_options("""
        var options = {
          "physics": { "forceAtlas2Based": { "gravitationalConstant": -50 }, "solver": "forceAtlas2Based" }
        }
        """)
        
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_file:
        net.save_graph(tmp_file.name)
        return tmp_file.name

def main():
    st.set_page_config(page_title="Liquid Assets Board - Invitation Tree", layout="wide")
    st.title("Liquid Assets Board - Invitation Tree 🍻")
    
    st.sidebar.header("Configuration")
    admin_name = st.sidebar.text_input("Replace 'You' with:", value="Jack")
    
    graph_style = st.sidebar.selectbox(
        "Select Graph Style (Overview Tab)",
        ("Sunburst Chart", "Treemap (Nested Blocks)", "Force-Directed (Hub & Spoke)", "Top-Down Hierarchical")
    )
    
    # Hide the uploader in a collapsed expander at the bottom of the sidebar
    with st.sidebar.expander("⚙️ Advanced", expanded=False):
        uploaded_file = st.file_uploader("Override default chat log (.txt)", type=["txt"])
    
    # Define the default path based on your folder structure
    default_file_path = "data/20261017 - WhatsApp Chat with Pints only, no chat 🍻.txt"
    content = None
    
    # Logic to handle the hidden upload override vs the default file
    if uploaded_file is not None:
        content = uploaded_file.getvalue().decode("utf-8")
    elif os.path.exists(default_file_path):
        with open(default_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    
    if content is None:
        st.error("No chat data found. Please ensure the default file is in the 'data' folder and pushed to GitHub.")
        return # Stop execution if no data is found

    with st.spinner("Parsing chat data..."):
        df_relationships, all_unique_names, active_members = parse_whatsapp_adds(content, admin_name)
        
    if df_relationships.empty:
        st.warning("No 'added' events found. Check if the file format matches standard WhatsApp exports.")
    else:
        clean_df = df_relationships.dropna(subset=['Adder', 'Added'])
        clean_df = clean_df[(clean_df['Adder'] != "") & (clean_df['Added'] != "")]
        G = nx.from_pandas_edgelist(clean_df, source='Adder', target='Added', create_using=nx.DiGraph())
        
        tab1, tab2 = st.tabs(["📊 Overview & Stats", "🔍 Investigate Lineage"])
        
        with tab1:
            with st.expander("1. Edit Connections (Manual Overrides)", expanded=False):
                st.write("Scroll to the bottom of the table to manually attribute members who joined via an invite link.")
                edited_df = st.data_editor(
                    df_relationships, 
                    num_rows="dynamic", 
                    use_container_width=True,
                    column_config={
                        "Adder": st.column_config.SelectboxColumn("Adder", options=all_unique_names, required=True),
                        "Added": st.column_config.SelectboxColumn("Added", options=all_unique_names, required=True)
                    },
                    key="relationship_editor"
                )
                
                clean_df = edited_df.dropna(subset=['Adder', 'Added'])
                clean_df = clean_df[(clean_df['Adder'] != "") & (clean_df['Added'] != "")]
                G = nx.from_pandas_edgelist(clean_df, source='Adder', target='Added', create_using=nx.DiGraph())
            
            st.subheader(f"2. {graph_style}")
            
            if graph_style in ["Top-Down Hierarchical", "Force-Directed (Hub & Spoke)"]:
                graph_html_path = render_pyvis_graph(G, graph_style)
                with open(graph_html_path, 'r', encoding='utf-8') as f:
                    html_data = f.read()
                components.html(html_data, height=750, scrolling=True)
                
            elif graph_style in ["Sunburst Chart", "Treemap (Nested Blocks)"]:
                plotly_df = prep_plotly_data(clean_df)
                
                if graph_style == "Sunburst Chart":
                    fig = px.sunburst(plotly_df, names='Added', parents='Adder', color_discrete_sequence=px.colors.qualitative.Pastel)
                else:
                    fig = px.treemap(plotly_df, names='Added', parents='Adder', color_discrete_sequence=px.colors.qualitative.Pastel)
                    
                fig.update_layout(margin=dict(t=0, l=0, r=0, b=0), height=700)
                st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("3. Top Recruiters")
            st.markdown(f"**Points (Active members only - {len(active_members)} currently in group):** Level 1 = 1pt, Level 2 = 0.5pts, Level 3 = 0.25pts.")
            
            points_df = calculate_invitation_points(G, active_members)
            if not points_df.empty:
                st.dataframe(points_df, use_container_width=True)
            else:
                st.info("No points calculated yet.")

        with tab2:
            st.markdown("### Find a member's invitation chain")
            search_member = st.selectbox("Select a member:", ["-- None --"] + all_unique_names, key="investigate_search")
            
            lineage = []
            if search_member != "-- None --":
                lineage = get_lineage(G, search_member)
                if lineage:
                    status = "🟢 Active" if search_member in active_members else "🔴 Removed/Left"
                    st.info(f"**Chain of Responsibility:** {' ➔ '.join(lineage)} | **Status:** {status}")
                else:
                    st.warning("Member not found in the invitation tree structure.")
            
            plotly_df_inv = prep_plotly_data(clean_df, search_member, lineage)
            colour_map = {"Target": "#ff3333", "Inviter Chain": "#ffcc00", "Other": "#444444", "Standard": None}
            
            fig_inv = px.sunburst(
                plotly_df_inv, 
                names='Added', 
                parents='Adder', 
                color='Highlight', 
                color_discrete_map=colour_map
            )
            fig_inv.update_layout(margin=dict(t=0, l=0, r=0, b=0), height=700)
            st.plotly_chart(fig_inv, use_container_width=True)

if __name__ == "__main__":
    main()