"use strict";(globalThis.webpackChunkketcher_react_app=globalThis.webpackChunkketcher_react_app||[]).push([[622],{25622(e,t,o){o.r(t),o.d(t,{default:()=>Gu});var n=o(84733),r=o(13017),i=o(60471),a=o(55830),s=o(5112),l=o(24530),d=o(94044),c=o(61923),p=o(97844),u=o(4056),h=o(22380),m=o.n(h),g=o(7256),f=o(7114),b=o(73189),x=o(17141),y=o(33903),v=o(14788),w=o(25973),A=o(73140),C=o(45025),M=o(28222),k=o(72328),I=o(49322),S=o(84764),Y=o(9913),L=o(63770),D=o(69475),E=o(35506),F=o(56040),N=o(82605),P=o(13677),B=o(93895),T=o(88368),R=o(52615),O=o(70055),z=o(63145),$=o(23698),j=o(20228),_=o(8422),H=o(88818),q=o(97466),V=o(85420),W=o(64795),U=o(25907),K=o(10694),G=o(37209),Q=o.n(G),Z=o(41047),X=o.n(Z),J=Object.defineProperty,ee=Object.getOwnPropertySymbols,te=Object.prototype.hasOwnProperty,oe=Object.prototype.propertyIsEnumerable,ne=(e,t,o)=>t in e?J(e,t,{enumerable:!0,configurable:!0,writable:!0,value:o}):e[t]=o,re=Symbol.for("immer-nothing"),ie=Symbol.for("immer-draftable"),ae=Symbol.for("immer-state");function se(e,...t){throw new Error(`[Immer] minified error nr: ${e}. Full error at: https://bit.ly/3cXEKWf`)}var le=Object.getPrototypeOf;function de(e){return!!e&&!!e[ae]}function ce(e){var t;return!!e&&(ue(e)||Array.isArray(e)||!!e[ie]||!!(null==(t=e.constructor)?void 0:t[ie])||be(e)||xe(e))}var pe=Object.prototype.constructor.toString();function ue(e){if(!e||"object"!==typeof e)return!1;const t=le(e);if(null===t)return!0;const o=Object.hasOwnProperty.call(t,"constructor")&&t.constructor;return o===Object||"function"==typeof o&&Function.toString.call(o)===pe}function he(e,t){0===me(e)?Reflect.ownKeys(e).forEach(o=>{t(o,e[o],e)}):e.forEach((o,n)=>t(n,o,e))}function me(e){const t=e[ae];return t?t.type_:Array.isArray(e)?1:be(e)?2:xe(e)?3:0}function ge(e,t){return 2===me(e)?e.has(t):Object.prototype.hasOwnProperty.call(e,t)}function fe(e,t,o){const n=me(e);2===n?e.set(t,o):3===n?e.add(o):e[t]=o}function be(e){return e instanceof Map}function xe(e){return e instanceof Set}function ye(e){return e.copy_||e.base_}function ve(e,t){if(be(e))return new Map(e);if(xe(e))return new Set(e);if(Array.isArray(e))return Array.prototype.slice.call(e);const o=ue(e);if(!0===t||"class_only"===t&&!o){const t=Object.getOwnPropertyDescriptors(e);delete t[ae];let o=Reflect.ownKeys(t);for(let n=0;n<o.length;n++){const r=o[n],i=t[r];!1===i.writable&&(i.writable=!0,i.configurable=!0),(i.get||i.set)&&(t[r]={configurable:!0,writable:!0,enumerable:i.enumerable,value:e[r]})}return Object.create(le(e),t)}{const t=le(e);if(null!==t&&o)return((e,t)=>{for(var o in t||(t={}))te.call(t,o)&&ne(e,o,t[o]);if(ee)for(var o of ee(t))oe.call(t,o)&&ne(e,o,t[o]);return e})({},e);const n=Object.create(t);return Object.assign(n,e)}}function we(e,t=!1){return Ce(e)||de(e)||!ce(e)||(me(e)>1&&(e.set=e.add=e.clear=e.delete=Ae),Object.freeze(e),t&&Object.entries(e).forEach(([e,t])=>we(t,!0))),e}function Ae(){se(2)}function Ce(e){return Object.isFrozen(e)}var Me,ke={};function Ie(e){const t=ke[e];return t||se(0),t}function Se(){return Me}function Ye(e,t){t&&(Ie("Patches"),e.patches_=[],e.inversePatches_=[],e.patchListener_=t)}function Le(e){De(e),e.drafts_.forEach(Fe),e.drafts_=null}function De(e){e===Me&&(Me=e.parent_)}function Ee(e){return Me={drafts_:[],parent_:Me,immer_:e,canAutoFreeze_:!0,unfinalizedDrafts_:0}}function Fe(e){const t=e[ae];0===t.type_||1===t.type_?t.revoke_():t.revoked_=!0}function Ne(e,t){t.unfinalizedDrafts_=t.drafts_.length;const o=t.drafts_[0];return void 0!==e&&e!==o?(o[ae].modified_&&(Le(t),se(4)),ce(e)&&(e=Pe(t,e),t.parent_||Te(t,e)),t.patches_&&Ie("Patches").generateReplacementPatches_(o[ae].base_,e,t.patches_,t.inversePatches_)):e=Pe(t,o,[]),Le(t),t.patches_&&t.patchListener_(t.patches_,t.inversePatches_),e!==re?e:void 0}function Pe(e,t,o){if(Ce(t))return t;const n=t[ae];if(!n)return he(t,(r,i)=>Be(e,n,t,r,i,o)),t;if(n.scope_!==e)return t;if(!n.modified_)return Te(e,n.base_,!0),n.base_;if(!n.finalized_){n.finalized_=!0,n.scope_.unfinalizedDrafts_--;const t=n.copy_;let r=t,i=!1;3===n.type_&&(r=new Set(t),t.clear(),i=!0),he(r,(r,a)=>Be(e,n,t,r,a,o,i)),Te(e,t,!1),o&&e.patches_&&Ie("Patches").generatePatches_(n,o,e.patches_,e.inversePatches_)}return n.copy_}function Be(e,t,o,n,r,i,a){if(de(r)){const a=Pe(e,r,i&&t&&3!==t.type_&&!ge(t.assigned_,n)?i.concat(n):void 0);if(fe(o,n,a),!de(a))return;e.canAutoFreeze_=!1}else a&&o.add(r);if(ce(r)&&!Ce(r)){if(!e.immer_.autoFreeze_&&e.unfinalizedDrafts_<1)return;Pe(e,r),t&&t.scope_.parent_||"symbol"===typeof n||!Object.prototype.propertyIsEnumerable.call(o,n)||Te(e,r)}}function Te(e,t,o=!1){!e.parent_&&e.immer_.autoFreeze_&&e.canAutoFreeze_&&we(t,o)}var Re={get(e,t){if(t===ae)return e;const o=ye(e);if(!ge(o,t))return function(e,t,o){var n;const r=$e(t,o);return r?"value"in r?r.value:null==(n=r.get)?void 0:n.call(e.draft_):void 0}(e,o,t);const n=o[t];return e.finalized_||!ce(n)?n:n===ze(e.base_,t)?(_e(e),e.copy_[t]=He(n,e)):n},has:(e,t)=>t in ye(e),ownKeys:e=>Reflect.ownKeys(ye(e)),set(e,t,o){const n=$e(ye(e),t);if(null==n?void 0:n.set)return n.set.call(e.draft_,o),!0;if(!e.modified_){const n=ze(ye(e),t),a=null==n?void 0:n[ae];if(a&&a.base_===o)return e.copy_[t]=o,e.assigned_[t]=!1,!0;if(((r=o)===(i=n)?0!==r||1/r===1/i:r!==r&&i!==i)&&(void 0!==o||ge(e.base_,t)))return!0;_e(e),je(e)}var r,i;return e.copy_[t]===o&&(void 0!==o||t in e.copy_)||Number.isNaN(o)&&Number.isNaN(e.copy_[t])||(e.copy_[t]=o,e.assigned_[t]=!0),!0},deleteProperty:(e,t)=>(void 0!==ze(e.base_,t)||t in e.base_?(e.assigned_[t]=!1,_e(e),je(e)):delete e.assigned_[t],e.copy_&&delete e.copy_[t],!0),getOwnPropertyDescriptor(e,t){const o=ye(e),n=Reflect.getOwnPropertyDescriptor(o,t);return n?{writable:!0,configurable:1!==e.type_||"length"!==t,enumerable:n.enumerable,value:o[t]}:n},defineProperty(){se(11)},getPrototypeOf:e=>le(e.base_),setPrototypeOf(){se(12)}},Oe={};function ze(e,t){const o=e[ae];return(o?ye(o):e)[t]}function $e(e,t){if(!(t in e))return;let o=le(e);for(;o;){const e=Object.getOwnPropertyDescriptor(o,t);if(e)return e;o=le(o)}}function je(e){e.modified_||(e.modified_=!0,e.parent_&&je(e.parent_))}function _e(e){e.copy_||(e.copy_=ve(e.base_,e.scope_.immer_.useStrictShallowCopy_))}he(Re,(e,t)=>{Oe[e]=function(){return arguments[0]=arguments[0][0],t.apply(this,arguments)}}),Oe.deleteProperty=function(e,t){return Oe.set.call(this,e,t,void 0)},Oe.set=function(e,t,o){return Re.set.call(this,e[0],t,o,e[0])};function He(e,t){const o=be(e)?Ie("MapSet").proxyMap_(e,t):xe(e)?Ie("MapSet").proxySet_(e,t):function(e,t){const o=Array.isArray(e),n={type_:o?1:0,scope_:t?t.scope_:Se(),modified_:!1,finalized_:!1,assigned_:{},parent_:t,base_:e,draft_:null,copy_:null,revoke_:null,isManual_:!1};let r=n,i=Re;o&&(r=[n],i=Oe);const{revoke:a,proxy:s}=Proxy.revocable(r,i);return n.draft_=s,n.revoke_=a,s}(e,t);return(t?t.scope_:Se()).drafts_.push(o),o}function qe(e){if(!ce(e)||Ce(e))return e;const t=e[ae];let o;if(t){if(!t.modified_)return t.base_;t.finalized_=!0,o=ve(e,t.scope_.immer_.useStrictShallowCopy_)}else o=ve(e,!0);return he(o,(e,t)=>{fe(o,e,qe(t))}),t&&(t.finalized_=!1),o}var Ve,We,Ue=new class{constructor(e){this.autoFreeze_=!0,this.useStrictShallowCopy_=!1,this.produce=(e,t,o)=>{if("function"===typeof e&&"function"!==typeof t){const o=t;t=e;const n=this;return function(e=o,...r){return n.produce(e,e=>t.call(this,e,...r))}}let n;if("function"!==typeof t&&se(6),void 0!==o&&"function"!==typeof o&&se(7),ce(e)){const r=Ee(this),i=He(e,void 0);let a=!0;try{n=t(i),a=!1}finally{a?Le(r):De(r)}return Ye(r,o),Ne(n,r)}if(!e||"object"!==typeof e){if(n=t(e),void 0===n&&(n=e),n===re&&(n=void 0),this.autoFreeze_&&we(n,!0),o){const t=[],r=[];Ie("Patches").generateReplacementPatches_(e,n,t,r),o(t,r)}return n}se(1)},this.produceWithPatches=(e,t)=>{if("function"===typeof e)return(t,...o)=>this.produceWithPatches(t,t=>e(t,...o));let o,n;return[this.produce(e,t,(e,t)=>{o=e,n=t}),o,n]},"boolean"===typeof(null==e?void 0:e.autoFreeze)&&this.setAutoFreeze(e.autoFreeze),"boolean"===typeof(null==e?void 0:e.useStrictShallowCopy)&&this.setUseStrictShallowCopy(e.useStrictShallowCopy)}createDraft(e){ce(e)||se(8),de(e)&&(e=function(e){de(e)||se(10);return qe(e)}(e));const t=Ee(this),o=He(e,void 0);return o[ae].isManual_=!0,De(t),o}finishDraft(e,t){const o=e&&e[ae];o&&o.isManual_||se(9);const{scope_:n}=o;return Ye(n,t),Ne(void 0,n)}setAutoFreeze(e){this.autoFreeze_=e}setUseStrictShallowCopy(e){this.useStrictShallowCopy_=e}applyPatches(e,t){let o;for(o=t.length-1;o>=0;o--){const n=t[o];if(0===n.path.length&&"replace"===n.op){e=n.value;break}}o>-1&&(t=t.slice(o+1));const n=Ie("Patches").applyPatches_;return de(e)?n(e,t):this.produce(e,e=>n(e,t))}};Ue.produce,Ue.produceWithPatches.bind(Ue),Ue.setAutoFreeze.bind(Ue),Ue.setUseStrictShallowCopy.bind(Ue),Ue.applyPatches.bind(Ue),Ue.createDraft.bind(Ue),Ue.finishDraft.bind(Ue),function(e){e.Monomer="monomer",e.Preset="preset",e.Bond="bond",e.AmbiguousMonomer="ambiguousMonomer",e.Text="text"}(Ve||(Ve={})),function(e){e.Library="library",e.ChainStart="chainStart",e.ChainMiddle="chainMiddle",e.ChainEnd="chainEnd"}(We||(We={}));const Ke="select-submenu",Ge={"select-rectangle":"rectangle","select-lasso":"lasso","select-structure":"structure"},Qe=e=>null!==e&&Object.prototype.hasOwnProperty.call(Ge,e);var Ze;!function(e){e.nanoMol="nM",e.microMol="\u03bcM",e.milliMol="mM"}(Ze||(Ze={}));const Xe={[Ze.nanoMol]:10**9,[Ze.microMol]:10**6,[Ze.milliMol]:1e3},Je={ketcherId:"",isReady:null,activeTool:"select",editor:void 0,monomerLibraryLoadError:null,editorLayoutMode:void 0,editorLineLength:c.Vru.editorLineLength,preview:{type:Ve.Monomer,monomer:void 0,style:{}},position:void 0,isContextMenuActive:!1,isDragging:!1,isMacromoleculesPropertiesWindowOpened:!1,macromoleculesProperties:void 0,unipositiveIonsMeasurementUnit:Ze.milliMol,oligonucleotidesMeasurementUnit:Ze.microMol,unipositiveIonsValue:140,oligonucleotidesValue:200,app:{buildDate:"2026-09-20T11:47:00",indigoVersion:"",indigoMachine:"",version:"3.20.0-rc.1"},selectedMenuGroupItems:{}},et=(0,p.Z0)({name:"editor",initialState:Je,reducers:{init:e=>{e.isReady=!1},initKetcherId:(e,t)=>{e.ketcherId=t.payload},initSuccess:e=>{e.isReady=!0},initFailure:e=>{e.isReady=!1},setMonomerLibraryLoadError:(e,t)=>{e.monomerLibraryLoadError=t.payload},selectTool:(e,t)=>{e.activeTool=t.payload,Qe(t.payload)&&(e.selectedMenuGroupItems[Ke]=t.payload)},setPosition:(e,t)=>{e.position=t.payload},createEditor:(e,t)=>{e.monomerLibraryLoadError=null;const o=new c.JxB({ketcherId:t.payload.ketcherId,theme:t.payload.theme,canvas:t.payload.canvas,renderersContainer:new c.gMS({theme:t.payload.theme})});o.initializeMonomersLibraryFromKetcher(t.payload.monomersLibraryUpdate,t.payload.monomersLibraryReplace,t.payload.onLibraryError),e.editor=o,t.payload.onInit?.(o)},destroyEditor:e=>{e.editorLayoutMode=e.editor?.mode.modeName,e.editor?.destroy(),e.editor=void 0},showPreview:(e,t)=>{e.preview=t.payload??{type:Ve.Monomer,monomer:void 0,style:{}}},setContextMenuActive:(e,t)=>{e.isContextMenuActive=t.payload},setIsDragging:(e,t)=>{e.isDragging=t.payload},setMacromoleculesPropertiesWindowVisibility:(e,t)=>{e.isMacromoleculesPropertiesWindowOpened=t.payload},toggleMacromoleculesPropertiesWindowVisibility:e=>{e.isMacromoleculesPropertiesWindowOpened=!e.isMacromoleculesPropertiesWindowOpened},setMacromoleculesProperties:(e,t)=>{e.macromoleculesProperties=t.payload},setUnipositiveIonsMeasurementUnit:(e,t)=>{e.unipositiveIonsMeasurementUnit=t.payload},setOligonucleotidesMeasurementUnit:(e,t)=>{e.oligonucleotidesMeasurementUnit=t.payload},setEditorLineLength:(e,t)=>{e.editorLineLength={...e.editorLineLength,...t.payload}},setUnipositiveIonsValue:(e,t)=>{e.unipositiveIonsValue=t.payload},setOligonucleotidesValue:(e,t)=>{e.oligonucleotidesValue=t.payload},setIndigoVersion:(e,t)=>{e.app.indigoVersion=t.payload}}}),{init:tt,initSuccess:ot,initFailure:nt,setMonomerLibraryLoadError:rt,initKetcherId:it,selectTool:at,setPosition:st,createEditor:lt,destroyEditor:dt,showPreview:ct,setContextMenuActive:pt,setIsDragging:ut,setMacromoleculesPropertiesWindowVisibility:ht,toggleMacromoleculesPropertiesWindowVisibility:mt,setMacromoleculesProperties:gt,setUnipositiveIonsMeasurementUnit:ft,setOligonucleotidesMeasurementUnit:bt,setEditorLineLength:xt,setUnipositiveIonsValue:yt,setOligonucleotidesValue:vt,setIndigoVersion:wt}=et.actions,At=e=>e.editor.preview,Ct=e=>e.editor.activeTool,Mt=e=>e.editor.ketcherId,kt=e=>e.editor.editor,It=e=>e.editor.editor?.isSequenceEditInRNABuilderMode,St=e=>e.editor.editor?.isSequenceMode,Yt=e=>e.editor.editorLayoutMode,Lt=e=>e.editor.editor?.isHandToolSelected,Dt=e=>e.editor.editor?.drawingEntitiesManager?.hasAntisenseChains,Et=e=>e.editor.isContextMenuActive,Ft=e=>e.editor.isDragging,Nt=e=>e.editor.isMacromoleculesPropertiesWindowOpened,Pt=e=>e.editor.macromoleculesProperties,Bt=e=>e.editor.unipositiveIonsMeasurementUnit,Tt=e=>e.editor.oligonucleotidesMeasurementUnit,Rt=e=>e.editor.unipositiveIonsValue,Ot=e=>e.editor.oligonucleotidesValue,zt=e=>e.editor.editorLineLength,$t=e=>e.editor.app,jt=e=>e.editor.selectedMenuGroupItems[Ke]||"select-rectangle",_t=et.reducer,Ht="FAVORITES",qt="PEPTIDE",Vt="CHEM",Wt="RNA";var Ut,Kt;!function(e){e.SUGARS="Sugars",e.BASES="Bases",e.PHOSPHATES="Phosphates",e.PEPTIDES="Amino Acids",e.NUCLEOTIDES="Nucleotides"}(Ut||(Ut={})),function(e){e.R="R",e.A="A",e.C="C",e.G="G",e.T="T",e.U="U",e.X="X",e.P="P"}(Kt||(Kt={})),Ut.SUGARS,Ut.BASES,Ut.BASES,Ut.BASES,Ut.BASES,Ut.BASES,Ut.BASES,Ut.PHOSPHATES;const Gt={A:"Ala",B:"Asx",C:"Cys",D:"Asp",E:"Glu",F:"Phe",G:"Gly",H:"His",I:"Ile",J:"Xle",K:"Lys",L:"Leu",M:"Met",N:"Asn",O:"Pyl",P:"Pro",Q:"Gln",R:"Arg",S:"Ser",T:"Thr",U:"Sec",V:"Val",W:"Trp",X:"Xaa",Y:"Tyr",Z:"Glx"},Qt="favoriteItemsUniqueKeys",Zt="ketcher_custom_presets",Xt="ketcher_preset_phosphate_filter",Jt="ketcher_polymer_sequence_type",eo="thymine",to="uracil",oo=1,no=2;const ro=new class{localStorage;constructor(){this.localStorage=window.localStorage}getItem(e){const t=this.localStorage.getItem(e);return t?JSON.parse(t):null}setItem(e,t){this.localStorage.setItem(e,JSON.stringify(t))}removeItem(e){this.localStorage.removeItem(e)}},io={[c.uP8.PEPTIDES]:c.IPc.AminoAcid,[c.uP8.BASES]:c.IPc.Base},ao={monomers:[],defaultRnaPresets:[],favorites:{},searchFilter:"",selectedTabIndex:no};function so(e){if((0,c.eoH)(e)){const t=e;return t.id||t.label}const t=e;return`${t.props.MonomerName}___${t.props?.Name}`}function lo(e){return`${e.name}_${e.base?.label??"."}_${e.sugar?.label??"."}_${e.phosphate?.label??"."}`}const co=(0,p.Z0)({name:"library",initialState:ao,reducers:{loadMonomerLibrary:(e,t)=>{const o=t.payload.map(e=>({...e,props:{...e.props}}));e.monomers=o},loadDefaultPresets:(e,t)=>{e.defaultRnaPresets=t.payload.map(e=>({...e}))},setFavoriteMonomersFromLocalStorage:e=>{const t={},o=ro.getItem(Qt);o&&Array.isArray(o)&&(e.monomers.forEach(e=>{const n=so(e);o.find(e=>e===n)&&(t[n]={...e,favorite:!0})}),e.favorites=t)},clearFavorites:e=>{e.favorites={}},toggleMonomerFavorites:(e,t)=>{const o=so(t.payload),n=ro.getItem(Qt)??[];e.favorites[o]?(delete e.favorites[o],ro.setItem(Qt,n.filter(e=>e!==o))):(e.favorites[o]={...t.payload,favorite:!0},n.push(o),ro.setItem(Qt,n))},setSearchFilter:(e,t)=>{e.searchFilter=t.payload},setSelectedTabIndex:(e,t)=>{e.selectedTabIndex=t.payload}}}),{loadMonomerLibrary:po,loadDefaultPresets:uo,setFavoriteMonomersFromLocalStorage:ho,clearFavorites:mo,toggleMonomerFavorites:go,setSearchFilter:fo,setSelectedTabIndex:bo}=co.actions,xo=(0,u.Mz)(e=>e.library.defaultRnaPresets,e=>(e??[]).reduce((e,t)=>(t.aliasAxoLabs&&t.name&&e.set(t.name.toLowerCase(),t.aliasAxoLabs.toLowerCase()),e),new Map)),yo=(e,t)=>e.filter(e=>!e.isAmbiguous&&e.props?.MonomerType===t),vo=(e,t)=>{const o=e.filter(e=>{if(!(0,c.eoH)(e))return!1;const o=new c.cN8(e,void 0,!1);return io[t]===o.monomerClass});if(0===o.length)return[];let n=[];t===c.uP8.BASES?n=[{groupTitle:"Ambiguous Bases",groupItems:o.filter(e=>(0,c.eoH)(e)&&e.options.every(e=>!e.templateId.toLowerCase().includes(eo)&&!e.templateId.toLowerCase().includes(to)))},{groupTitle:"Ambiguous DNA Bases",groupItems:o.filter(e=>(0,c.eoH)(e)&&e.options.find(e=>e.templateId.toLowerCase().includes(eo)))},{groupTitle:"Ambiguous RNA Bases",groupItems:o.filter(e=>(0,c.eoH)(e)&&e.options.find(e=>e.templateId.toLowerCase().includes(to)))}]:n.push({groupTitle:`Ambiguous ${t}`,groupItems:o});const r=["X","N"];return n.forEach(e=>{e.groupItems.sort((e,t)=>e.label.localeCompare(t.label)),e.groupItems.sort(e=>r.includes(e.label)?-1:1)}),n},wo=e=>e.filter(e=>e.favorite&&!e.isAmbiguous),Ao=(0,u.Mz)(e=>e.library,e=>{const{searchFilter:t,monomers:o,favorites:n}=e,r=t.toLowerCase(),i=(e,{idtAliases:t,name:o="",fullName:n="",helmAlias:r="",bilnAlias:i="",axoLabsAlias:a="",modificationTypes:s=[],oneLetterCode:l,isAminoAcid:d})=>{const c=o.toLowerCase(),p=n.toLowerCase(),u=t?.base?.toLowerCase(),h=t?.modifications?Object.values(t.modifications).map(e=>e.toLowerCase()).join(" "):"",m=r?.toLowerCase()??"",g=i?.toLowerCase()??"",f=a?.toLowerCase()??"",b=s&&s.length>0?s.map(e=>e.toLowerCase()).join(" "):"";if("/"===e)return Boolean(u||h);if(e.includes("/")){const o=e.split("/");if(o.length>3||3===o.length&&""!==o[2])return!1;if(3===o.length&&""!==o[1]){const e=o[1],t=u?.length===e.length&&Array.from(u).every((t,o)=>t===e[o]),n=!!h&&h.split(" ").some(t=>t.length===e.length&&Array.from(t).every((t,o)=>t===e[o]));return t||n}const n=o[0],r=o[1];if(e.startsWith("/")&&e.length>1){const o={5:"endpoint5",3:"endpoint3",i:"internal"}[e[1]];if(!o){const t=e.slice(1);return u?.startsWith(t)||h?.split(" ").some(e=>e.startsWith(t))}const n=t?.modifications?.[o]?.toLowerCase(),r=e.slice(2),i=r?Boolean(u?.startsWith(r)):Boolean(n),a=!!n&&n.includes(e);return i||a}if(e.endsWith("/")&&e.length>1){const t=e.slice(0,-1);return u?.endsWith(t)||h?.split(" ").some(e=>e.endsWith(t))}const i=u?.startsWith(r)&&u?.endsWith(n),a=!!h&&h.split(" ").some(e=>e.startsWith(r)&&e.endsWith(n));return i||a}const x=!!u&&u.includes(e),y=!!h&&h.includes(e),v=!!m&&m.includes(e),w=!!g&&g.includes(e),A=!!f&&f.includes(e),C=!!b&&b.includes(e),M=((e,t,o)=>{if(!t||!e)return!1;const n=Gt[e.toUpperCase()];return!!n&&n.toLowerCase().includes(o)})(l,d,e);return c.includes(e)||p.includes(e)||x||y||v||w||A||C||M};return o.filter(e=>{if(!e.isAmbiguous&&e.props?.hidden)return!1;if(e.isAmbiguous){const{label:t,id:o,idtAliases:n,monomers:a}=e,s=a.length>0&&a.every(e=>e.monomerItem.props.MonomerClass===c.IPc.AminoAcid);return i(r,{idtAliases:n,name:t,fullName:o,oneLetterCode:t,isAminoAcid:s})||a.some(e=>{const{Name:t,MonomerName:o,MonomerClass:n,idtAliases:a,aliasHELM:s,aliasBILN:l,aliasAxoLabs:d,modificationTypes:p}=e.monomerItem.props;return i(r,{idtAliases:a,name:t,fullName:o,helmAlias:s,bilnAlias:l,axoLabsAlias:d,modificationTypes:p,oneLetterCode:o,isAminoAcid:n===c.IPc.AminoAcid})})}{const{Name:t,MonomerName:o,MonomerClass:n,idtAliases:a,aliasHELM:s,aliasBILN:l,aliasAxoLabs:d,modificationTypes:p}=e.props;return i(r,{idtAliases:a,name:t,fullName:o,helmAlias:s,bilnAlias:l,axoLabsAlias:d,modificationTypes:p,oneLetterCode:o,isAminoAcid:n===c.IPc.AminoAcid})}}).map(e=>({...e,favorite:!!n[so(e)]}))}),Co=e=>{const t=e.reduce((e,t)=>{const o=t.props.MonomerNaturalAnalogCode||"Z";return e[o]||(e[o]=[]),e[o].push({...t,label:t.props.MonomerName}),e},{}),o=Object.entries(t).reduce((e,[t,o])=>{const n=[...o];n.sort((e,t)=>e.label.localeCompare(t.label));const r=n.findIndex(e=>e.label===t);if(-1!==r){const e=n.splice(r,1);n.unshift(e[0])}return e[t]=n,e},{}),n=Object.keys(o);return n.sort((e,t)=>e.localeCompare(t)),n.reduce((e,t)=>{const n={groupTitle:"Z"===t?"No natural analogue":t,groupItems:[]};return o[t].forEach(e=>{n.groupItems.push({...e,props:{...e.props}})}),n.groupItems.length&&e.push(n),e},[])},Mo=e=>e.library.selectedTabIndex,ko=co.reducer,Io=e=>e.library.defaultRnaPresets,So=(0,p.Z0)({name:"modal",initialState:{name:null,isOpen:!1,additionalProps:null,errorTooltips:[],errorModalText:"",errorModalTitle:""},reducers:{openModal:(e,t)=>{"string"===typeof t.payload?e.name=t.payload:(e.name=t.payload.name,e.additionalProps=t.payload.additionalProps),e.isOpen=!0},closeModal:e=>{e.name=null,e.isOpen=!1,e.additionalProps=null},openErrorTooltip:(e,t)=>{e.errorTooltips.includes(t.payload)||e.errorTooltips.push(t.payload)},closeErrorTooltip:(e,t)=>{e.errorTooltips=t.payload?e.errorTooltips.filter(e=>e!==t.payload):[]},openErrorModal:(e,t)=>{if("string"===typeof t.payload)e.errorModalText=t.payload;else{const{errorMessage:o,errorTitle:n}=t.payload;e.errorModalText=o,e.errorModalTitle=n}},closeErrorModal:e=>{e.errorModalText=""}}}),{openModal:Yo,closeModal:Lo,openErrorTooltip:Do,closeErrorTooltip:Eo,openErrorModal:Fo,closeErrorModal:No}=So.actions,Po=e=>e.modal.name,Bo=e=>e.modal.isOpen,To=e=>e.modal.additionalProps,Ro=e=>e.modal.errorTooltips,Oo=e=>e.modal.errorModalText,zo=e=>e.modal.errorModalTitle,$o=So.reducer,jo=e=>t=>o=>{const n=t(o);return Do.match(o)&&setTimeout(()=>{e.dispatch(Eo(o.payload))},6e3),n},_o=()=>ro.getItem(Zt),Ho=e=>{const t=_o();return t?.findIndex(t=>t.name===e)??-1},qo=e=>{const t={...e},o=_o()??[],n=Ho(t.nameInList);t.nameInList=t.name,n>-1?(o.splice(n,1,t),ro.setItem(Zt,o)):ro.setItem(Zt,[...o,t])},Vo=(e,t)=>Boolean(e?.props?.MonomerCaps&&t in e.props.MonomerCaps),Wo=e=>({is3PrimeAvailable:(!e?.sugar||Vo(e.sugar,"R2"))&&(!e?.phosphate||Vo(e.phosphate,"R1")),is5PrimeAvailable:(!e?.sugar||Vo(e.sugar,"R1"))&&(!e?.phosphate||Vo(e.phosphate,"R2"))}),Uo=/^[a-zA-Z0-9-_*]+$/;var Ko;!function(e){e.Presets="Presets"}(Ko||(Ko={}));const Go={activePreset:null,sequenceSelection:void 0,sequenceSelectionName:void 0,isSequenceFirstsOnlyNucleoelementsSelected:void 0,activePresetMonomerGroup:null,groupItemValidations:{[Ut.BASES]:[],[Ut.SUGARS]:[],[Ut.PHOSPHATES]:[]},presetsDefault:[],presetsCustom:[],activeRnaBuilderItem:null,activeMonomerKey:null,isEditMode:!1,uniqueNameError:"",invalidPresetError:"",invalidPresetNameError:"",activePresetForContextMenu:null,presetPhosphateFilter:(()=>{const e=ro.getItem(Xt);return e&&"object"===typeof e&&"boolean"===typeof e.fivePrime&&"boolean"===typeof e.threePrime&&"boolean"===typeof e.noPhosphate?e:{fivePrime:!1,threePrime:!1,noPhosphate:!1}})()},Qo={[Ut.BASES]:"base",[Ut.SUGARS]:"sugar",[Ut.PHOSPHATES]:"phosphate"},Zo=(0,p.Z0)({name:"rna-builder",initialState:Go,reducers:{createNewPreset:e=>{e.activePreset={base:void 0,sugar:void 0,phosphate:void 0,name:"",nameInList:""}},setActivePreset:(e,t)=>{e.activePreset={...t.payload,nameInList:t.payload.name}},setSequenceSelection:(e,t)=>{e.sequenceSelection=[...t.payload]},setSequenceSelectionName:(e,t)=>{e.sequenceSelectionName=t.payload},setIsSequenceFirstsOnlyNucleoelementsSelected:(e,t)=>{e.isSequenceFirstsOnlyNucleoelementsSelected=t.payload},setActivePresetForContextMenu:(e,t)=>{e.activePresetForContextMenu=t.payload},setPresetPhosphateFilter:(e,t)=>{e.presetPhosphateFilter=t.payload,ro.setItem(Xt,t.payload)},setActivePresetName:(e,t)=>{e.activePreset&&(e.activePreset.name=t.payload)},setActiveRnaBuilderItem:(e,t)=>{e.activeRnaBuilderItem=t.payload},recalculateRnaBuilderValidations:(e,t)=>{const{sugarValidations:o,phosphateValidations:n,baseValidations:r}=((e,t,o)=>{const n=[],r=[],i=[];if(!t||!o&&!e?.sugar&&!e?.phosphate&&!e?.base)return{sugarValidations:n,phosphateValidations:r,baseValidations:i};const{is3PrimeAvailable:a,is5PrimeAvailable:s}=Wo(e);return"right"===o?(n.push("R2"),r.push("R1")):"left"===o?(n.push("R1"),r.push("R2")):(s||(r.push("R1"),n.push("R2")),a||(r.push("R2"),n.push("R1"))),e?.base&&n.push("R3"),i.push("R1"),e?.sugar?.props?.MonomerCaps&&!Vo(e.sugar,"R3")&&i.push("DISABLED"),{sugarValidations:n,phosphateValidations:r,baseValidations:i}})(t.payload.rnaPreset,t.payload.isEditMode,t.payload.selectedPhosphatePosition??(0,c.uVP)(t.payload.rnaPreset));e.groupItemValidations[Ut.SUGARS]=o,e.groupItemValidations[Ut.BASES]=r,e.groupItemValidations[Ut.PHOSPHATES]=n},setActivePresetMonomerGroup:(e,t)=>{e.activePresetMonomerGroup=t.payload?t.payload:null},savePreset:(e,t)=>{const o={...t.payload};if(qo((e=>{const t=["sugar","base","phosphate"],o=m()(e,t);o.templates=[];for(const n of t){const t=e[n],r=t?.props?.id??t?.id;r&&o.templates.push({$ref:t.isAmbiguous?(0,c.QDi)(r):(0,c.aRw)(r)})}return o.connections=(0,c.Qkd)(e,(0,c.uVP)(e)),o})(o)),o.nameInList){const t=e.presetsCustom.findIndex(e=>e.name===o.nameInList);o.nameInList=o.name,-1===t?e.presetsCustom.push(o):e.presetsCustom.splice(t,1,o)}else e.presetsCustom.push(o);e.activePreset&&(e.activePreset.nameInList=o.name)},deletePreset:(e,t)=>{const o=t.payload;(e=>{if(!e)return;const t=_o(),o=Ho(e);t&&(t.splice(o,1),t.length?ro.setItem(Zt,t):ro.removeItem(Zt))})(o.name);const n=e.presetsCustom.findIndex(e=>e.name===o.name);e.presetsCustom.splice(n,1),o.nameInList&&(e.activePreset=null)},setIsEditMode:(e,t)=>{e.isEditMode=t.payload},setUniqueNameError:(e,t)=>{e.uniqueNameError=t.payload},setInvalidPresetError:(e,t)=>{e.invalidPresetError=t.payload},setInvalidPresetNameError:(e,t)=>{e.invalidPresetNameError=t.payload},setDefaultPresets:(e,t)=>{const o=t.payload[0];if(!o)return;e.presetsDefault.find(e=>e.name===o.name)||(e.presetsDefault=t.payload)},setCustomPresets:(e,t)=>{e.presetsCustom=t.payload},setFavoritePresetsFromLocalStorage:e=>{const t=ro.getItem(Qt);t&&Array.isArray(t)&&(e.presetsDefault=e.presetsDefault.map(e=>{const o=`${e.name}_${c.QKJ.RNA}`;return t.find(e=>e===o)?{...e,favorite:!0}:e}))},clearFavorites:e=>{e.presetsDefault=[]},setActiveMonomerKey:(e,t)=>{e.activeMonomerKey=t.payload},togglePresetFavorites:(e,t)=>{const o=e.presetsDefault.findIndex(e=>e.name===t.payload.name),n=e.presetsCustom.findIndex(e=>e.name===t.payload.name);if(o>=0){const t=e.presetsDefault[o].favorite;e.presetsDefault[o].favorite=!t}else if(n>=0){(e=>{if(!e)return;const t=_o(),o=Ho(e);t&&o>-1&&(t[o].favorite=!t[o].favorite,ro.setItem(Zt,t))})(e.presetsCustom[n].name);const t=e.presetsCustom[n].favorite;return void(e.presetsCustom[n].favorite=!t)}const r=`${t.payload.name}_${c.QKJ.RNA}`,i=ro.getItem(Qt)??[];i.some(e=>e===r)?ro.setItem(Qt,i.filter(e=>e!==r)):(i.push(r),ro.setItem(Qt,i))}}}),Xo=e=>e.rnaBuilder.activeRnaBuilderItem,Jo=e=>e.rnaBuilder.groupItemValidations,en=e=>e.rnaBuilder.activePreset,tn=e=>e.rnaBuilder.sequenceSelection,on=e=>e.rnaBuilder.sequenceSelectionName,nn=e=>e.rnaBuilder.isSequenceFirstsOnlyNucleoelementsSelected,rn=(e,t)=>{if(Qo[t]&&e)return e[Qo[t]]},an=e=>e.rnaBuilder.activePresetMonomerGroup,sn=e=>Boolean(e.name&&e.sugar&&(e.base||e.phosphate)),ln=e=>e.rnaBuilder.isEditMode,dn=e=>{if(!e)return"";const t=e.sugar?.label??e.sugar?.props.MonomerName??"",o=e.base?.label??e.base?.props.MonomerName??"",n=e.phosphate?.label??e.phosphate?.props.MonomerName??"";let r=t;return r+=t&&n||(t||n)&&o?`(${o})`:o,n&&(r+=n),r},cn=e=>e.rnaBuilder.uniqueNameError,pn=e=>e.rnaBuilder.invalidPresetError,un=e=>e.rnaBuilder.invalidPresetNameError,hn=e=>{const t=e.rnaBuilder.activePreset;return t&&!t.nameInList&&!t.name&&!t.sugar&&!t.base&&!t.phosphate},mn=e=>e.rnaBuilder.activePresetForContextMenu,gn=e=>e.rnaBuilder.presetPhosphateFilter,fn=e=>e.filter(e=>e.favorite),bn=e=>e.rnaBuilder.activeMonomerKey,xn=(0,u.Mz)(e=>e.rnaBuilder,e=>{const{presetsDefault:t=[],presetsCustom:o=[]}=e;return[...t,...o]}),yn=(0,u.Mz)(xn,e=>e.library.searchFilter,xo,gn,(e,t,o,n)=>{const r=t.toLowerCase();return e.filter(e=>{const t=e.name?.toLowerCase(),n=e.sugar?.label?.toLowerCase(),i=e.phosphate?.label?.toLowerCase(),a=e.base?.label?.toLowerCase(),s=e.idtAliases?.base?.toLowerCase(),l=e.aliasAxoLabs?.toLowerCase()??(t?o.get(t):void 0)??"",d=e.idtAliases?.modifications,c=d?Object.values(d).filter(e=>"string"===typeof e):[];let p=s;if(s&&e.name?.includes("MOE")){const e=s;p=`${d?.endpoint5??`5${e}`}, ${d?.internal??`i${e}`}`}const u=(r.match(/\//g)??[]).length,h=r.split("/");if(u>=2&&void 0!==h[2]&&""!==h[2])return!1;if(r.startsWith("/")&&r.length>1){const e=r.slice(1);return p?.toLowerCase().startsWith(e)||s?.startsWith(e)||c.some(t=>t.toLowerCase().startsWith(e))}if(r.endsWith("/")&&r.length>1){const e=r.slice(0,-1),t=r[r.length-2];return p?.toLowerCase().endsWith(e)&&p[p.length-1]===t||s?.endsWith(e)&&s[s.length-1]===t||c.some(o=>o.toLowerCase().endsWith(e)&&o[o.length-1]===t)}return"/"===r?!!e.idtAliases:t?.includes(r)||n?.includes(r)||i?.includes(r)||a?.includes(r)||p?.toLowerCase().includes(r)||l.includes(r)}).filter(e=>{if(!n)return!0;const{fivePrime:t,threePrime:o,noPhosphate:r}=n;if(t&&o&&r||!t&&!o&&!r)return!0;if(!e.phosphate)return r;return"left"===(0,c.uVP)(e)?t:o})}),{setActivePreset:vn,setSequenceSelection:wn,setSequenceSelectionName:An,setIsSequenceFirstsOnlyNucleoelementsSelected:Cn,setActivePresetName:Mn,setActiveRnaBuilderItem:kn,setActiveMonomerKey:In,recalculateRnaBuilderValidations:Sn,setActivePresetMonomerGroup:Yn,savePreset:Ln,deletePreset:Dn,createNewPreset:En,setIsEditMode:Fn,setUniqueNameError:Nn,setInvalidPresetError:Pn,setInvalidPresetNameError:Bn,setDefaultPresets:Tn,setCustomPresets:Rn,setActivePresetForContextMenu:On,setPresetPhosphateFilter:zn,togglePresetFavorites:$n,setFavoritePresetsFromLocalStorage:jn,clearFavorites:_n}=Zo.actions,Hn=Zo.reducer;function qn(e={}){return(0,p.U1)({reducer:{editor:_t,modal:$o,library:ko,rnaBuilder:Hn},middleware:e=>e({serializableCheck:!1}).concat(jo),preloadedState:e})}qn();const Vn={colorA:{regular:"#5ADC11",hover:"#4FC218"},colorCM:{regular:"#59D0FF",hover:"#3CB9EB"},colorDQ:{regular:"#AD4551",hover:"#AB0014"},colorEN:{regular:"#93F5F5",hover:"#00F0F0"},colorFY:{regular:"#5656BF",hover:"#2626BF"},colorGX:{regular:"#FFE97B",hover:"#F8DC50"},colorH:{regular:"#BFC9FF",hover:"#99AAFF"},colorILV:{regular:"#50E576",hover:"#00D936"},colorKR:{regular:"#365CFF",hover:"#002CEB"},colorP:{regular:"#F2C5B6",hover:"#FFA98C"},colorST:{regular:"#FF8D8D",hover:"#ED6868"},colorW:{regular:"#99458B",hover:"#7F006B"},colorU:{regular:"#FF973C",hover:"#2EE55D"},colorX:{regular:"#CAD3DD",hover:"#B8BBCC"},chem:{regular:"#333333",hover:"#555555"},default:{regular:"#CCCBD6",hover:"#B8BBCC"}},Wn={D:{regular:"#FF8C69",hover:"#0097A8"},E:{regular:"#DC143C",hover:"#0097A8"},K:{regular:"#B0E0E6",hover:"#0097A8"},H:{regular:"#007FFF",hover:"#0097A8"},O:{regular:"#2A52BE",hover:"#0097A8"},R:{regular:"#0A12FF",hover:"#0097A8"},Q:{regular:"#EDB4ED",hover:"#0097A8"},Y:{regular:"#D65CBC",hover:"#0097A8"},U:{regular:"#CA7DE3",hover:"#0097A8"},S:{regular:"#9966CC",hover:"#0097A8"},C:{regular:"#BF00FF",hover:"#0097A8"},N:{regular:"#800080",hover:"#0097A8"},T:{regular:"#FF00FF",hover:"#0097A8"},L:{regular:"#7FFF00",hover:"#0097A8"},I:{regular:"#4CBB17",hover:"#0097A8"},F:{regular:"#008A00",hover:"#0097A8"},A:{regular:"#008080",hover:"#0097A8"},W:{regular:"#50E576",hover:"#0097A8"},P:{regular:"#D2D900",hover:"#0097A8"},G:{regular:"#BDB76B",hover:"#0097A8"},M:{regular:"#FFF600",hover:"#0097A8"},V:{regular:"#FFD700",hover:"#0097A8"}},Un={color:{background:{canvas:"#F5F5F5",primary:"#FFFFFF",secondary:"#F8FEFF",overlay:"rgba(94,94,94,.8)"},border:{primary:"#CAD3DD",secondary:"#7C7C7F"},text:{primary:"#333333",secondary:"#167782",light:"#585858",dark:"#000000",error:"#FF4A4A",lightgrey:"#7C7C7F"},tab:{regular:"#FFFFFF",active:"#E1E5EA",hover:"#00838F",content:"#EFF2F5"},scroll:{regular:"#717171",inactive:"#DDDDDD"},button:{primary:{active:"#167782",hover:"#00838F",clicked:"#4FB3BF",disabled:"rgba(113, 113, 113, 0.4)"},secondary:{active:"#585858",hover:"#333333",clicked:"#AEAEAE",disabled:"rgba(113, 113, 113, 0.4)"},group:{active:"#167782",hover:"#2E858F"},transparent:{active:"transparent"},text:{primary:"#FFFFFF",secondary:"#005662",disabled:"#7A7A7A"}},dropdown:{primary:"#333333",secondary:"#FFFFFF",hover:"#DDDDDD",disabled:"#7A7A7A"},tooltip:{background:"#FFFFFF",text:"#333333"},link:{active:"#00838F",hover:"#005662"},divider:"#AEAEAE",spinner:"#005662",error:"#FF4A4A",input:{text:{default:"#585858",active:"#333333",disabled:"#585858",error:"#FF4A4A"},background:{primary:"#FFFFFF",default:"#EFF2F5",hover:"#DDDDDD",disabled:"#eff2f5"},border:{regular:"#cad3dd",active:"#FFFFFF",hover:"#43b5c0",focus:"#EFF2F5",error:"#FF4A4A"}},icon:{grey:"#B4B9D6",hover:"#005662",active:"#525252",activeMenu:"#005662",clicked:"#FFFFFF",disabled:"rgba(82, 82, 82, 0.4)"},monomer:{default:"#C8C8C8"},editMode:{sequenceInRNABuilder:"#99d7dc"}},font:{size:{small:"10px",regular:"12px",medium:"14px",xsmall:"6px"},family:{montserrat:"Montserrat, sans-serif",inter:"Inter, FreeSans, Arimo, 'Droid Sans', Helvetica, 'Helvetica Neue',\nArial, sans-serif",roboto:"Roboto, FreeSans, Arimo, Droid Sans, Helvetica, Helvetica Neue, Arial, sans-serif"},weight:{light:300,regular:400,bold:600}},monomer:{color:{A:Vn.colorA,C:Vn.colorCM,M:Vn.colorCM,D:Vn.colorDQ,Q:Vn.colorDQ,E:Vn.colorEN,N:Vn.colorEN,F:Vn.colorFY,Y:Vn.colorFY,G:Vn.colorGX,X:Vn.colorX,Other:Vn.colorX,H:Vn.colorH,I:Vn.colorILV,L:Vn.colorILV,V:Vn.colorILV,K:Vn.colorKR,R:Vn.colorKR,P:Vn.colorP,S:Vn.colorST,T:Vn.colorST,W:Vn.colorW,U:Vn.colorU,CHEM:Vn.chem,default:Vn.default}},peptide:{color:{D:Wn.D,E:Wn.E,K:Wn.K,H:Wn.H,O:Wn.O,R:Wn.R,Q:Wn.Q,Y:Wn.Y,U:Wn.U,S:Wn.S,C:Wn.C,N:Wn.N,T:Wn.T,L:Wn.L,I:Wn.I,F:Wn.F,A:Wn.A,W:Wn.W,P:Wn.P,G:Wn.G,M:Wn.M,V:Wn.V,Other:Vn.colorX}},border:{regular:"1px solid #CAD3DD",small:"1px solid #E1E5EA",radius:{regular:"4px"}},shadow:{regular:"0px 1px 1px rgba(197, 203, 207, 0.7)",mainLayoutBlocks:"0px 2px 5px rgba(103, 104, 132, 0.15)"},outline:{small:"1px solid #B4B9D6",medium:"2px solid #B4B9D6",color:"#B4B9D6",selected:{color:"#167782",small:"1px solid #167782",medium:"2px solid #167782"},grey:{small:"1px solid #585858"}},transition:{regular:"all .3s"},zIndex:{base:0,toolbar:10,sticky:100,overlay:200,modal:1e3,critical:9999}},Kn=e=>(0,a.AH)({all:"unset",".Ketcher-polymer-editor-root":{all:"unset",fontSize:e.ketcher.font.size.medium,fontFamily:e.ketcher.font.family.inter,fontWeight:e.ketcher.font.weight.regular,backgroundColor:e.ketcher.color.background.primary,color:e.ketcher.color.text.primary,boxSizing:"border-box"},":where(.Ketcher-polymer-editor-root) div":{boxSizing:"border-box"},":where(.Ketcher-polymer-editor-root) input":{fontFamily:e.ketcher.font.family.inter,fontWeight:e.ketcher.font.weight.regular,fontSize:e.ketcher.font.size.regular,boxSizing:"border-box"},":where(.Ketcher-polymer-editor-root) h1":{fontSize:96},":where(.Ketcher-polymer-editor-root) h2":{fontSize:60},":where(.Ketcher-polymer-editor-root) h3":{fontSize:48},":where(.Ketcher-polymer-editor-root) h4":{fontSize:34},":where(.Ketcher-polymer-editor-root) h5":{fontSize:24},":where(.Ketcher-polymer-editor-root) h6":{fontSize:20,fontWeight:e.ketcher.font.weight.bold},":where(.Ketcher-polymer-editor-root) p":{fontSize:e.ketcher.font.size.regular},":where(.Ketcher-polymer-editor-root) button":{textTransform:"uppercase",fontWeight:e.ketcher.font.weight.bold}}),Gn=g.A.div`
  width: ${"254px"};
  height: calc(100% - 16px);
  display: flex;
  flex-direction: column;
  background-color: ${({theme:e})=>e.ketcher.color.background.primary};
  box-shadow: ${({theme:e})=>e.ketcher.shadow.mainLayoutBlocks};
  border-radius: 4px;
  z-index: ${({theme:e})=>e.ketcher.zIndex.toolbar};
`,Qn=g.A.div`
  padding: 10px 0 10px 8px;
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
`,Zn=g.A.div`
  height: 24px;
  display: flex;
  flex-grow: 1;
  gap: 4px;
  align-items: center;
  padding: 4px;
  background-color: ${({theme:e})=>e.ketcher.color.input.background.default};
  border-radius: 4px;

  &:hover,
  &:has(input:focus) {
    outline: ${({theme:e})=>e.ketcher.outline.selected.small};
  }
`,Xn=g.A.button`
  height: 24px;
  display: flex;
  align-items: center;
  gap: 2px;
  border: none;
  border-radius: 4px 0 0 4px;
  cursor: pointer;
  background-color: #e2e5e9;
  text-transform: none;
  font-weight: ${({theme:e})=>e.ketcher.font.weight.regular};
  font-size: ${({theme:e})=>e.ketcher.font.size.regular};
  color: ${({theme:e})=>e.ketcher.color.text.secondary};
`,Jn=(0,g.A)(f.In)`
  height: 16px;
  width: 16px;
  color: ${({theme:e})=>e.ketcher.color.text.secondary};
`,er=(0,g.A)(f.pd)`
  flex-grow: 1;
  padding: 0;
  margin: 0;
  background-color: transparent;
  border: none;
  outline: none;

  &:hover {
    outline: none;
  }

  &:focus {
    outline: none;
  }
`,tr=g.A.div(({fullWidth:e,withPaddingRight:t})=>({width:e?"100%":"fit-content",display:"flex",flexDirection:"column",justifyContent:"space-between",paddingRight:t?"12px":0,overflow:e?"hidden":"initial"})),or=g.A.div(({theme:e})=>({height:"100%",width:"100%",position:"relative",padding:"12px",paddingBottom:0,backgroundColor:e.ketcher.color.background.canvas,display:"flex",justifyContent:"space-between",containerType:"size",overflow:"clip"})),nr=g.A.div(({theme:e})=>({height:"100%",width:"100%",position:"relative",paddingBottom:0,backgroundColor:e.ketcher.color.background.canvas,display:"flex",justifyContent:"space-between",columnGap:"3px"})),rr=g.A.div(({hide:e=!1})=>({height:"100%",width:"fit-content",display:e?"none":"flex",flexDirection:"column"})),ir=(0,g.A)(rr)``,ar=(0,g.A)(rr)``,sr=g.A.div`
  display: flex;
  justify-content: space-between;
  // cqw is used to ensure that the width is calculated based on the container(ketcher root) width, allowing for proper scrolling behavior
  // with 100% width scrollbar does not work properly for some reason
  width: 100cqw;
`,lr=g.A.div(({shortened:e=!1,theme:t})=>({height:"36px",width:e?"100%":"calc(100% - 100px)",marginBottom:"6px",display:"flex",alignItems:"center",backgroundColor:"#FFFFFF",boxShadow:t.ketcher.shadow.mainLayoutBlocks,borderRadius:"4px",overflowX:"hidden"})),dr=g.A.div`
  width: 42px;
  height: 36px;
  display: flex;
  position: relative;
  right: 0;
  cursor: pointer;
  background: white;
  box-shadow: ${({theme:e})=>e.ketcher.shadow.mainLayoutBlocks};
  border-radius: 4px;
`,cr=g.A.div`
  &:not(:empty) {
    margin-bottom: 15px;
  }
`,pr=g.A.div({height:"100%",width:"100%",position:"relative",overflow:"hidden"}),ur=g.A.div({}),hr=g.A.div({height:"40px"}),mr=e=>{const[t,o]=(0,b.Wx)({threshold:1}),[r,a]=(0,b.Wx)({threshold:1}),{children:s,...l}=e,d=(0,i.useRef)(null);return(0,n.FD)("div",{style:{display:"flex",position:"relative"},children:[(0,n.FD)(lr,{...l,ref:d,children:[(0,n.Y)("span",{ref:t}),(0,n.Y)(sr,{children:(0,n.Y)(n.FK,{children:s})}),(0,n.Y)("span",{ref:r})]}),o&&a?null:(0,n.Y)(dr,{children:(0,n.Y)(f.Nn,{startInView:o,endInView:a,scrollBack:e=>{d.current&&(d.current.scrollLeft-=300*e/1e3)},scrollForward:e=>{d.current&&(d.current.scrollLeft+=300*e/1e3)},isLeftRight:!0})})]})},gr=({children:e})=>{const t={Left:null,Main:null,Right:null,Top:null,Bottom:null,InsideRoot:null};return i.Children.forEach(e,e=>{e.type===ir?t.Left=e:e.type===ar?t.Right=e:e.type===mr?t.Top=e:e.type===cr?t.Bottom=e:e.type===pr?t.Main=e:e.type===ur&&(t.InsideRoot=e)}),(0,n.FD)(or,{children:[(0,n.FD)(tr,{fullWidth:!0,withPaddingRight:!0,children:[t.Top,(0,n.FD)(nr,{children:[t.Left,(0,n.Y)(hr,{}),t.Main]}),t.Bottom]}),(0,n.Y)(tr,{children:t.Right}),t.InsideRoot]})};gr.Left=ir,gr.Top=mr,gr.Bottom=cr,gr.Right=ar,gr.Main=pr,gr.InsideRoot=ur;var fr="TabPanel-module_tabPanelDiv__wMffZ",br="TabPanel-module_tabPanelBox__LY-BB";const xr=({children:e,value:t,index:o})=>(0,n.Y)("div",{className:fr,role:"tabpanel",hidden:t!==o,id:o.toString(),children:t===o&&(0,n.Y)(x.A,{className:br,children:(0,n.Y)(n.FK,{children:e})})}),yr=(0,g.A)(y.A,{shouldForwardProp:e=>"isLayoutToRight"!==e})`
  height: 32px;
  min-height: 32px;
  list-style-type: none;
  margin: 0;
  padding: 4px 8px 0 8px;
  border-bottom: ${({theme:e})=>`1px solid ${e.ketcher.color.border.primary}`};
  overflow: unset;

  & .MuiTabs-scroller,
  & .MuiTabs-flexContainer {
    height: 100%;
    overflow: unset !important;
  }

  & .MuiTabs-flexContainer {
    justify-content: ${({isLayoutToRight:e})=>e?"flex-end":"flex-start"};
    padding-right: ${({isLayoutToRight:e})=>e?"16px":"0"};
  }

  & .MuiTabs-indicator {
    display: none;
  }
`,vr=(0,g.A)(v.A,{shouldForwardProp:e=>"isLayoutToRight"!==e})`
  min-height: 24px;
  min-width: 0;
  position: relative;
  padding: 7px 12px;
  font-size: ${({theme:e})=>e.ketcher.font.size.regular};
  text-transform: none;
  cursor: pointer;
  text-align: center;
  background-color: ${({theme:e})=>e.ketcher.color.tab.regular};
  color: ${({theme:e})=>e.ketcher.color.text.light};
  list-style-type: none;
  margin-left: 1px;
  align-items: center;
  flex: ${({isLayoutToRight:e})=>e?void 0:"1 1 auto;"};
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: 4px 4px 0 0;

  &:first-of-type {
    margin-left: 0;
  }

  &:hover {
    background-color: ${({theme:e})=>e.ketcher.color.tab.regular};
    color: ${({theme:e})=>e.ketcher.color.text.primary};
    border-color: ${({theme:e})=>e.ketcher.color.border.primary};
  }

  &.Mui-selected {
    background-color: ${({theme:e})=>e.ketcher.color.tab.active};
    color: ${({theme:e})=>e.ketcher.color.text.primary};
    border-color: ${({theme:e})=>e.ketcher.color.border.primary};
    margin-bottom: -1px;
    padding-bottom: 8px;

    &::after {
      content: '';
      position: absolute;
      left: 0;
      bottom: -1px; /* Adjust to cover the parent's bottom border */
      height: 1px;
      background-color: ${({theme:e})=>e.ketcher.color.tab.active};
    }
  }

  &[data-tab='Favorites'] {
    font-size: 16px;
    color: #faa500;
  }
`,wr=(0,g.A)(v.A)`
  width: 0;
  height: 0;
  min-width: 0;
  min-height: 0;
  padding: 0;
  margin: 0;
  visibility: hidden;
`,Ar=g.A.div`
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  justify-content: flex-start;
  width: 100%;
  height: 100%;
`,Cr=e=>({id:`simple-tab-${e}`,"aria-controls":`simple-tabpanel-${e}`});var Mr=(0,i.memo)(e=>{const{tabs:t,selectedTabIndex:o,isLayoutToRight:r,onChange:i}=e,a=t[o],s=a?.component,l=a?.props;return(0,n.FD)(n.FK,{children:[(0,n.FD)(yr,{value:o,onChange:i,isLayoutToRight:r,children:[t.map((e,t)=>(0,n.Y)(vr,{label:e.caption,title:e.tooltip,isLayoutToRight:r,"data-testid":e.testId,"data-tab":e.tooltip,...Cr(t)},e.caption||e.testId)),(0,n.Y)(wr,{value:-1})]}),a&&(0,n.Y)(xr,{value:o,index:o,children:(0,n.Y)(Ar,{children:(0,n.Y)(s,{...l})})})]})});const kr=()=>{},Ir=(e,t,o)=>{const n=new Map(e.map(e=>{let t;if((0,c.eoH)(e)){const o=e;t=(0,c.QDi)(o.id)}else{const o=e;t=(0,c.aRw)(o.props.id||so(o))}return[t,e]}));return t.filter(e=>e.templates.every(e=>Boolean(n.get(e.$ref)))).map(e=>{const t=new Map(e.templates.map(e=>{const t=n.get(e.$ref),[,,o]=(0,c.w7j)(t);return[o,t]})),r=t.get(c.IPc.Sugar),i=t.get(c.IPc.Base),a=t.get(c.IPc.Phosphate),s=e.connections??(0,c.Qkd)({base:i,sugar:r,phosphate:a});return{...{base:i?{...i,label:i.label}:void 0,name:e.name,phosphate:a?{...a,label:a.label}:void 0,connections:s,sugar:r?{...r,label:r.label}:void 0,phosphatePosition:(0,c.uVP)({sugar:r,phosphate:a,connections:s}),favorite:e.favorite,default:o||e.default},...e.idtAliases&&{idtAliases:e.idtAliases},...e.aliasAxoLabs&&{aliasAxoLabs:e.aliasAxoLabs}}})},Sr=e=>Object.entries(e).filter(([e,t])=>Boolean(t)).map(([e])=>e),Yr=e=>e?e.replace(/(?:^\/+)|(?:\/+$)/g,""):e,Lr=()=>(0,r.wA)(),Dr=r.d4;function Er(){const e=Dr(Mt),t=Dr(kt),o=Dr(Yt);let n;try{n=c.qd$.getKetcher(e)}catch(p){c.$yr.error(`Failed to get ketcher instance with id ${e}`,p)}const r=n?.editor?.struct().isBlank(),a=r?c.jJA:c.cmw,[s,l]=(0,i.useState)(o||a),d=(0,i.useCallback)(e=>{l(e)},[]);return(0,i.useEffect)(()=>(t?.events.layoutModeChange.add(d),()=>{d(c.jJA),t?.events.layoutModeChange.remove(d)}),[d,t]),s}function Fr(){const e=Dr(kt),t=Dr(It),[o,n]=(0,i.useState)(t),r=(0,i.useCallback)(e=>{n(e)},[]);return(0,i.useEffect)(()=>(e?.events.toggleSequenceEditInRNABuilderMode.add(r),()=>{e?.events.toggleSequenceEditInRNABuilderMode.remove(r)}),[r,e]),o}const Nr=i.createContext({});function Pr(e,t){const o=(0,i.useRef)(e),n=(0,i.useRef)(void 0);(0,i.useEffect)(()=>{o.current=e},[e]),(0,i.useEffect)(()=>(n.current=(0,d.debounce)((...e)=>o.current(...e),t),()=>{n.current?.cancel(),n.current=void 0}),[t]);return{debouncedCallback:(0,i.useCallback)((...e)=>n.current?.(...e),[]),invokeImmediately:(0,i.useCallback)((...e)=>o.current(...e),[]),cancel:(0,i.useCallback)(()=>n.current?.cancel(),[])}}const Br=(0,i.createContext)({width:0,height:0}),Tr=({children:e,rootRef:t,isMacromoleculesEditorTurnedOn:o})=>{const[r,a]=(0,i.useState)({width:0,height:0}),s=(0,i.useCallback)(()=>{const e=t?.current;if(!e)return;const{width:o,height:n}=e.getBoundingClientRect();a({width:o,height:n})},[t]),{debouncedCallback:l,cancel:d}=Pr(s,100);return(0,i.useEffect)(()=>{s()},[s,o]),(0,i.useEffect)(()=>(window.addEventListener("resize",l),()=>{window.removeEventListener("resize",l),d()}),[d,l]),(0,n.Y)(Br.Provider,{value:r,children:e})},Rr=()=>{const{height:e}=(0,i.useContext)(Br);return e<720},Or=()=>{const e=Lr(),t=(0,i.useMemo)(()=>(0,d.debounce)(t=>e(ct(t)),500),[e]),o=(0,i.useCallback)(()=>{t.cancel(),e(ct(void 0))},[t,e]);return(0,i.useEffect)(()=>()=>t.cancel(),[t]),{showPreview:t,closePreview:o}},zr=e=>{if(e)return Array.isArray(e)?e.join(", "):e},$r=g.A.div`
  background: white;
  height: 48px;
  text-align: center;
  cursor: ${({disabled:e,isDragging:t})=>e?"default":t?"grabbing !important":"pointer"};
  opacity: ${({disabled:e})=>e?"0.4":"1"};
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: ${({theme:e})=>e.ketcher.font.size.small};
  color: ${({theme:e})=>e.ketcher.color.text.primary};
  position: relative;
  overflow: hidden;
  border-radius: 4px;
  box-shadow: 0 2px 5px 0 rgba(103, 104, 132, 0.149);
  margin: 0;
  user-select: none;
  border-color: #167782;
  border-width: ${({selected:e})=>e?"2px 2px 2px":"0px"};
  border-style: solid;
  box-sizing: border-box;
  z-index: ${({selected:e})=>e?2:void 0};

  ${({isDragging:e})=>e&&"\n    &, & * {\n      pointer-events: none !important;\n      cursor: grabbing !important;\n    }\n  "}

  .hidden & .star {
    visibility: hidden !important;
  }

  &:hover {
    outline: 1px solid #b4b9d6;
    > .star,
    .autochain {
      visibility: visible;
      opacity: 1;
    }
  }
  &::after {
    content: '';
    display: block;
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 8px;
    border-bottom: ${({isVariantMonomer:e})=>e?"1px solid #CAD3DD":"none"};
    background: ${({code:e,theme:t,item:o})=>{if(!o)return t.ketcher.monomer.color.default?.regular;const n=o;if("PEPTIDE"===n.props?.MonomerType&&t.ketcher.peptide.color[e]?.regular)return t.ketcher.peptide.color[e]?.regular;return t.ketcher.monomer.color[e]?.regular||t.ketcher.monomer.color.default?.regular}};
  }
  ,
  > span {
    position: absolute;
    bottom: ${({selected:e})=>e?"4px":"6px"};
    left: ${({selected:e})=>e?"4px":"6px"};
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 85%;
  }
  > .star {
    color: #cad3dd;
    position: absolute;
    left: calc(50% - 7px);
    top: 11px;
    font-size: 13px;
    line-height: 13px;
    opacity: 0;
    transition: 0.2s ease;
    flex-shrink: 0;
    border: 0;
    background: transparent;
    padding: 0;
    cursor: pointer;
    &.visible {
      visibility: visible;
      opacity: 1;
    }
    &:active {
      transform: scale(1.4);
    }
    &:hover,
    &.visible {
      color: #faa500;
    }
  }
`,jr=g.A.div`
  display: flex;
  justify-content: center;
  align-items: center;
  height: 15px;
  width: ${({monomersAmount:e})=>e>=10?"20px":"15px"};
  border-radius: ${({monomersAmount:e})=>e>=10?"20px":"50%"};
  border: 1px solid #cceaee;
  position: absolute;
  bottom: ${({selected:e})=>e?"4px":"6px"};
  left: ${({selected:e})=>e?"18px":"20px"};
  font-size: 12px;
  line-height: 12px;
`,_r=g.A.span`
  font-size: 12px;
`,Hr=(0,g.A)(f.In)`
  color: #cad3dd;
  stroke-width: 0;
  opacity: 0;
  transition: 0.2s ease;
  flex-shrink: 0;
  width: 13px;

  &:active {
    transform: scale(1.2);
  }
  &:hover {
    color: ${({disabled:e})=>e?"#cad3dd":"#333333"};
  }
`,qr=(0,g.A)("div")({position:"absolute",top:"12px",left:"4px"}),Vr=(e,t)=>{const o=(0,r.d4)(kt),n=(0,r.wA)();(0,i.useEffect)(()=>{if(!o||!t.current)return;const r=(0,$.Ltv)(t.current),i=(0,$.$Er)().on("start",()=>{o.isLibraryItemDragCancelled="sequence-layout-mode"===o.mode.modeName,o.isLibraryItemDragCancelled||(document.body.style.cursor="grabbing")}).on("drag",t=>{if(o.isLibraryItemDragCancelled)return;n(ut(!0));const{clientX:r,clientY:i}=t.sourceEvent;o.events.setLibraryItemDragState.dispatch({item:e,position:{x:r-(o.ketcherRootElementBoundingClientRect?.left||0),y:i-(o.ketcherRootElementBoundingClientRect?.top||0)}})}).on("end",t=>{if(!o.isLibraryItemDragCancelled){const{clientX:n,clientY:r}=t.sourceEvent,i=c.OQ2.instance.canvasWrapper.node()?.getBoundingClientRect();if(i){const{top:t,left:a,right:s,bottom:l}=i,d=c.OQ2.instance.zoomTransform,p=n-a+15*d.k,u=r-t+15*d.k,[h,m]=d.invert([p,u]);n>=a&&n<=s&&r>=t&&r<=l&&(o.events.setLibraryItemDragState.dispatch({item:e,position:{x:n-(o.ketcherRootElementBoundingClientRect?.left||0),y:r-(o.ketcherRootElementBoundingClientRect?.top||0)}}),o.events.placeLibraryItemOnCanvas.dispatch(e,{x:h,y:m}))}}o.events.setLibraryItemDragState.dispatch(null),o.isLibraryItemDragCancelled=!1,document.body.style.cursor="",n(ut(!1))});return r.call(i),()=>{r.on(".drag",null)}},[o,e,t,n])},Wr=(e,t)=>{const{selectedMonomersWithFreeR2:o,selectedMonomers:n}=e.getDataForAutochain();return n.length>0&&1!==o.length?"Select a monomer or a chain that has one R2 available.":1!==o.length||(0,c.fdJ)(t)?"":"This monomer cannot be added to a chain using this button, as it lacks R1."},Ur=(e,t,o)=>{o(Wr(e,t))},Kr="autochain",Gr=({item:e,groupName:t,onMouseLeave:o,onMouseMove:a,isSelected:s,disabled:l,onClick:d=kr,onStarClick:p=kr})=>{const u=Lr(),h=Dr(kt),m=Dr(St),g=Dr(Ft),[f,b]=(0,i.useState)(""),x=(0,i.useRef)(null),y=((e,t)=>{const o=Dr(It),n=(0,r.d4)(nn);return!!o&&!!e?.props?.MonomerCaps&&(t===c.uP8.BASES?!e?.props?.MonomerCaps?.R1:t===c.uP8.PHOSPHATES?!(e?.props?.MonomerCaps?.R1&&e?.props?.MonomerCaps?.R2):t===c.uP8.SUGARS&&(n?!(e?.props?.MonomerCaps?.R3&&e?.props?.MonomerCaps?.R2):!(e?.props?.MonomerCaps?.R3&&e?.props?.MonomerCaps?.R2&&e?.props?.MonomerCaps?.R1)))})(e,t)||l;let v="";(0,c.eoH)(e)||(v=e.props.MonomerType===Vt?e.props.MonomerType:e.props.MonomerNaturalAnalogCode);const w=so(e),A=(0,c.eoH)(e)?void 0:e,C=(0,i.useCallback)(t=>{t.stopPropagation(),p(),u(go(e))},[u,e,p]),M=(0,i.useCallback)(t=>{if(t.stopPropagation(),h){const t=Wr(h,e);if(b(t),t)return}h?.events.autochain.dispatch(e)},[h,e]),k=(0,i.useCallback)(()=>h&&Ur(h,e,b),[h,e]),I=(0,i.useCallback)(()=>{if(h){const t=Wr(h,e);if(b(t),t)return}h?.events.previewAutochain.dispatch(e)},[h,e]),S=(0,i.useCallback)(()=>{h?.events.removeAutochainPreview.dispatch(e)},[h,e]);return Vr(e,x),(0,n.FD)($r,{selected:s,disabled:y,isDragging:g,"data-testid":w,"data-monomer-item-id":w,item:A,isVariantMonomer:e.isAmbiguous,code:v,onMouseOver:k,onMouseLeave:o,onMouseMove:a,onDoubleClick:e=>{M(e),S()},...y?{}:{onClick:d},ref:x,"data-idtalias-base":A?.props.idtAliases?.base??void 0,"data-idtalias-modifications-endpoint5":A?.props.idtAliases?.modifications?.endpoint5??void 0,"data-idtalias-modifications-endpoint3":A?.props.idtAliases?.modifications?.endpoint3??void 0,"data-idtalias-modifications-internal":A?.props.idtAliases?.modifications?.internal??void 0,"data-axolabs":A?.props.aliasAxoLabs??void 0,"data-helm":A?.props.aliasHELM??void 0,"data-biln":A?.props.aliasBILN??void 0,"data-modificationtype":zr(A?.props.modificationTypes),children:[(0,n.Y)(_r,{children:e.label}),!y&&(0,n.FD)(n.FK,{children:[!m&&(0,n.Y)(j.A,{title:f,children:(0,n.Y)(qr,{children:(0,n.Y)(Hr,{className:Kr,name:"monomer-autochain",disabled:Boolean(f),onMouseOver:I,onMouseOut:S,onClick:M,onDoubleClick:e=>e.stopPropagation()})})}),(0,n.Y)("button",{type:"button",onClick:C,className:"star "+(e.favorite?"visible":""),"aria-label":"Toggle favorite",children:"\u2605"})]}),(0,c.eoH)(e)&&(0,n.Y)(jr,{selected:s,monomersAmount:e.monomers.length,children:e.monomers.length})]})},Qr=g.A.div`
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  grid-template-rows: auto;
  flex: 1;
  gap: 4px;

  &::after {
    content: '';
    flex: auto;
  }
`,Zr=g.A.div`
  position: relative;
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  justify-content: flex-start;
  font-size: ${({theme:e})=>e.ketcher.font.size.small};
  font-family: ${({theme:e})=>e.ketcher.font.family.roboto};
  color: ${({theme:e})=>e.ketcher.color.divider};
  margin: 0;
  gap: 4px;
`,Xr=(0,g.A)(Zr)`
  flex-direction: column;
`,Jr=g.A.div`
  height: 100%;
  display: flex;
  flex-grow: 0;
  flex-basis: 14px;
  flex-direction: column;
  flex-wrap: wrap;
  justify-content: flex-start;
  font-size: ${({theme:e})=>e.ketcher.font.size.medium};
  font-family: ${({theme:e})=>e.ketcher.font.family.roboto};
  color: ${({theme:e})=>e.ketcher.color.text.primary};
  margin: 0;
`,ei=[Kr],ti=e=>ei.some(t=>e.classList.contains(t)||e.closest(`.${t}`)),oi=({items:e,title:t,groupName:o,selectedMonomerUniqueKey:r,libraryName:i,disabled:a,onItemClick:s=kr})=>{const l=Dr(kt),d=Dr(Jo),p=Fr(),u=e=>{if(a)return a;if((0,c.eoH)(e)){if(p)return!1;const e=o?d[o]:void 0;return e?.includes("DISABLED")??!1}const t=e,n=d[`${t.props?.MonomerClass}s`];if(n?.length>0&&t.props?.MonomerCaps)for(const o of n)if(!(o in t.props.MonomerCaps))return!0;return!1},{showPreview:h,closePreview:m}=Or(),g=e=>r===so(e);return e&&0!==e.length?(0,n.FD)(Xr,{children:[t&&(0,n.Y)(Jr,{children:t}),(0,n.Y)(Qr,{children:e.map(e=>(0,n.Y)(Gr,{disabled:u(e),item:e,groupName:o,isSelected:g(e),onMouseLeave:m,onMouseMove:t=>((e,t)=>{if(m(),ti(t.target))return;const o=t.currentTarget.getBoundingClientRect();if((0,c.eoH)(e)){const t=(0,f.Xv)(e)(o),n=`${o.left+o.width/2}px`;h({type:Ve.AmbiguousMonomer,monomer:e,style:{left:n,top:t,transform:"translate(-50%, 0)"}})}else{const t=(0,f.Dk)(o);h({type:Ve.Monomer,monomer:e,style:{right:"-88px",top:t,transform:"translate(-50%, 0)"}})}})(e,t),onClick:()=>(e=>{["FAVORITES","PEPTIDE","CHEM"].includes(i??"")&&l?.events.selectMonomer.dispatch(e),s(e)})(e),onStarClick:m},so(e)))})]}):null},ni=g.A.div({width:"100%",display:"flex",flexDirection:"column",justifyItems:"center",gap:"8px",padding:"8px"}),ri=(0,g.A)($r)`
  &::after {
    content: '';
    background: ${({theme:e,selected:t})=>t?e.ketcher.color.button.primary.active:"#faa500"};
  }

  .dots {
    visibility: hidden;
    position: absolute;
    right: 2px;
    top: 10px;
  }

  &:hover .dots {
    visibility: visible;
  }

  > .star {
    right: 0;
    left: calc(50% - 7px);
    top: 11px;
    width: min-content;
  }
`,ii=g.A.div(e=>({minHeight:"32px",display:"flex",alignItems:"center",padding:"8px 12px",gap:"8px",borderBottom:e.theme.ketcher.border.small})),ai=g.A.span(e=>({flexGrow:1,fontSize:e.theme.ketcher.font.size.regular})),si=(0,g.A)(f.In,{shouldForwardProp:e=>"expanded"!==e})(e=>({width:"16px",height:"16px",color:e.theme.ketcher.color.icon.grey,transition:e.theme.ketcher.transition.regular,transform:e.expanded?"rotate(180deg)":"none"}));var li,di=(0,i.memo)(({preset:e,isSelected:t,onClick:o=kr,onContextMenu:r=kr,onMouseLeave:a=kr,onMouseMove:s=kr,onStarClick:l=kr})=>{const d=Lr(),c=Dr(kt),p=Dr(St),u=Dr(Ft),[h,m]=(0,i.useState)(""),g=(0,i.useRef)(null),f=(0,i.useCallback)(t=>{t.stopPropagation(),l(),d($n(e))},[d,e,l]),b=(0,i.useCallback)(t=>{t.stopPropagation(),h||c?.events.autochain.dispatch(e)},[h,c,e]),x=(0,i.useCallback)(()=>c&&Ur(c,e,m),[c,e]),y=(0,i.useCallback)(()=>{h||c?.events.previewAutochain.dispatch(e)},[h,c,e]),v=(0,i.useCallback)(()=>{c?.events.removeAutochainPreview.dispatch(e)},[c,e]);return Vr(e,g),(0,n.FD)(ri,{"data-testid":lo(e),onClick:o,onContextMenu:r,onMouseOver:x,onMouseLeave:a,onMouseMove:s,onDoubleClick:e=>{b(e),v()},selected:t,isDragging:u,code:e.name,"data-rna-preset-item-name":e.name,ref:g,children:[!p&&(0,n.Y)(j.A,{title:h,children:(0,n.Y)(qr,{children:(0,n.Y)(Hr,{className:Kr,name:"monomer-autochain",disabled:Boolean(h),onMouseOver:y,onMouseOut:v,onClick:b,onDoubleClick:e=>e.stopPropagation()})})}),(0,n.Y)("span",{children:e.name}),(0,n.Y)(si,{name:"vertical-dots",className:"dots",onClick:r}),(0,n.Y)("div",{"aria-hidden":!0,onClick:f,className:"star "+(e.favorite?"visible":""),children:"\u2605"})]})});!function(e){e.FOR_RNA="context-menu-for-RNA",e.FOR_SEQUENCE="context-menu-for-sequence",e.FOR_POLYMER_BOND="context-menu-for-polymer-bond",e.FOR_SELECTED_MONOMERS="context-menu-for-selected-monomers"}(li||(li={}));const ci=(0,g.A)(q.W1)`
  --contexify-activeItem-bgColor: rgba(243, 245, 247, 1);
  --contexify-menu-minWidth: 140px;
  --contexify-activeItem-color: rgba(51, 51, 51, 1);
  --contexify-menu-padding: 4px;
  --contexify-itemContent-padding: 6px 8px;
  --contexify-separator-margin: 4px 0;
  --contexify-separator-color: #e1e5ea;
  .contexify_itemContent {
    font-family: ${({theme:e})=>e.ketcher.font.family.inter};
    font-size: ${({theme:e})=>e.ketcher.font.size.regular};
    height: 28px;
  }
  .contexify_item-title {
    opacity: 1;
    font-weight: bold;
    background: #e1e5ea;
    margin: -4px 0 4px -4px;
    width: calc(100% + 8px);
    border-radius: 4px 4px 0 0;
  }
  .context_menu-icon {
    width: 16px;
    height: 16px;
    display: flex;
    align-items: center;
    margin-right: 4px;
  }
  .context_menu-text {
    display: flex;
    align-items: center;
    line-height: ${({theme:e})=>e.ketcher.font.size.regular};
  }
  .context_menu-delete-text {
    display: flex;
    align-items: center;
    line-height: ${({theme:e})=>e.ketcher.font.size.regular};
    margin-left: -3px;
  }
`,pi=(e,t)=>{let o=!1;const r=[];return e.forEach(({name:e,title:i,icon:a,hidden:s,disabled:l,isMenuTitle:d,separator:c,subMenuItems:p,onMouseOver:u,onMouseOut:h})=>{const m=p?.length?(0,n.Y)(q.Qw,{label:i,"data-testid":e,children:pi(p,t)},e):(0,n.FD)(q.q7,{id:e,onClick:e=>{o=!0,setTimeout(()=>{o=!1},500),t(e)},"data-testid":e,hidden:s,disabled:l,className:d?"contexify_item-title":"",onMouseOver:()=>{o||u?.(e)},onMouseOut:()=>h?.(e),children:[a&&(0,n.Y)("span",{className:"context_menu-icon",children:a}),(0,n.Y)("span",{className:"delete"===e?"context_menu-delete-text":"context_menu-text",children:i})]},e);r.push(m),c&&r.push((0,n.Y)(q.wv,{},`separator-${e}`))}),r},ui=({id:e,handleMenuChange:t,menuItems:o})=>{const r=Lr(),a=Dr(Et);return(0,i.useEffect)(()=>{const e=e=>{e.target?.__data__?.node||e.target?.__data__?.monomer?e.stopPropagation():r(pt(!1))};return document.addEventListener("click",e),document.addEventListener("contextmenu",e),()=>{document.removeEventListener("click",e),document.removeEventListener("contextmenu",e)}},[r,e]),(0,i.useEffect)(()=>{const e=e=>{"Escape"===e.key&&a&&(e.preventDefault(),e.stopPropagation(),q.He.hideAll(),r(pt(!1)))};return document.addEventListener("keydown",e,!0),()=>{document.removeEventListener("keydown",e,!0)}},[r,a]),(0,n.Y)(ci,{id:e,children:pi(o,t)})},hi=()=>{const e=no,t=Lr(),o=Dr(mn),r=Dr(Mo),i=Dr(It),a=[{name:"duplicateandedit",title:"Duplicate and Edit...",disabled:!1},{name:"edit",title:"Edit...",separator:!0,disabled:o?.default},{name:"deletepreset",title:"Delete Preset",disabled:o?.default}],s=document.querySelector(f.tS);return s&&!i?(0,H.createPortal)((0,n.Y)(ui,{id:li.FOR_RNA,menuItems:a,handleMenuChange:({id:n,props:i})=>{switch(n){case"duplicateandedit":i.duplicatePreset(o),r!==e&&t(bo(e));break;case"edit":i.editPreset(o),r!==e&&t(bo(e));break;case"deletepreset":t(Yo("delete"))}}}),s):null},mi=({presets:e,duplicatePreset:t,editPreset:o})=>{const i=Dr(en),a=Dr(kt),{show:s}=(0,q.EF)({id:li.FOR_RNA}),l=(0,r.wA)(),d=e=>e.phosphatePosition??(0,c.uVP)(e),p=e=>()=>{const t=(e=>{let t=!0,o=!0,n=!0;return e?.base?.props?.MonomerCaps&&(t="R1"in e.base.props.MonomerCaps),e?.sugar?.props?.MonomerCaps&&t&&e?.base?.props?.MonomerCaps&&(o="R3"in e.sugar.props.MonomerCaps),e?.phosphate?.props?.MonomerCaps&&(n="R1"in e.phosphate.props.MonomerCaps,o&&e?.sugar?.props?.MonomerCaps&&(o="R2"in e.sugar.props.MonomerCaps)),t&&o&&n})(e);t||!e.name?(l(vn(e)),a?.events.selectPreset.dispatch(e),e.name!==i.name&&l(Fn(!1))):l(Pn(e.name))},u=e=>{!(e instanceof HTMLDivElement)&&e.parentElement&&(e=e.parentElement);const t=e.getBoundingClientRect(),o=e.offsetParent?.getBoundingClientRect();let n=t.right-140;const r=t.y+t.height/2;return o?.x&&o?.x>n&&(n=t.x),{x:n,y:r}},h=Dr(At),{showPreview:m,closePreview:g}=Or(),b=e=>n=>{n.stopPropagation(),l(On(e)),s({event:n,props:{duplicatePreset:t,editPreset:o},position:u(n.currentTarget)})};return(0,n.FD)(Xr,{"data-testid":"rna-preset-group",children:[(0,n.Y)(Qr,{children:e.map((e,t)=>(0,n.Y)(di,{isSelected:i?.name===e.name,preset:e,onClick:p(e),onContextMenu:b(e),onMouseMove:t=>((e,t)=>{if(g(),ti(t.target))return;if(h.type===Ve.Preset||!t.currentTarget)return;const o=[e.sugar,e.base,e.phosphate],n=t.currentTarget.getBoundingClientRect(),r={left:`${n.left+n.width}px`,top:(0,c.eoH)(e.base)?(0,f.Xv)(e.base)(n):(0,f._Y)(n),transform:"translate(-100%, 0)"},i=(0,c.eoH)(e.base)?{type:Ve.AmbiguousMonomer,monomer:e.base,presetMonomers:o,style:r}:{type:Ve.Preset,monomers:o,name:e.name,idtAliases:e.idtAliases,aliasAxoLabs:e.aliasAxoLabs,phosphatePosition:d(e),position:We.Library,style:r};m(i)})(e,t),onMouseLeave:g,onStarClick:g},`${e.name}${t}`))}),(0,n.Y)(hi,{})]})},gi=({onItemClick:e,libraryName:t,duplicatePreset:o,editPreset:r})=>{const i=Dr(Ao),a=Dr(yn),s=t===Ht,l=s?{monomers:wo(i),presets:fn(a)}:yo(i,t),d=Co(s?l.monomers:l),p=s?(e=>{let t=[];for(const o in c.uP8){const n=vo(e,c.uP8[o]);t=[...t,...n]}return t.forEach(e=>{e.groupItems=e.groupItems.filter(e=>e.favorite)}),t.filter(e=>e.groupItems.length)})(i):vo(i,Ut.PEPTIDES);return(0,n.FD)(ni,{children:[s&&d.length>0&&(0,n.Y)("div",{children:"Monomers"}),d.map(({groupItems:o,groupTitle:r},i,a)=>(0,n.Y)(oi,{title:1===a.length?void 0:r,items:o,libraryName:t,onItemClick:e},r)),s&&l.presets.length>0&&(0,n.FD)(n.FK,{children:[(0,n.Y)("div",{children:"Presets"}),(0,n.Y)(mi,{duplicatePreset:o,editPreset:r,presets:l.presets})]}),(0,n.Y)(n.FK,{children:("PEPTIDE"===t||s)&&p.map(o=>(0,n.Y)(oi,{title:o.groupTitle,items:o.groupItems,libraryName:t,onItemClick:e},o.groupTitle))})]})},fi=g.A.div`
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
  padding: 8px;
  background-color: #f7f9fa;
  border-radius: 0 0 4px 4px;
`,bi=g.A.span`
  font-size: ${e=>e.theme.ketcher.font.size.medium};
  overflow: hidden;
  text-overflow: ellipsis;
  color: ${e=>e.theme.ketcher.color.text.primary};
`,xi=g.A.div`
  display: flex;
  align-items: center;
  width: 100%;
`;var yi=(0,i.memo)(({name:e,fullName:t})=>{const o=e??t;if(!o)return null;const r=t??o;return(0,n.Y)(fi,{title:r,children:(0,n.Y)(xi,{children:(0,n.Y)(bi,{title:r,"aria-label":r,children:o})})})});const vi=(0,g.A)("div",{shouldForwardProp:e=>"selected"!==e&&"isEditMode"!==e})(e=>{const{selected:t,isEditMode:o,theme:n}=e;let r="transparent";return t?r=n.ketcher.color.button.primary.active:o&&(r=n.ketcher.color.background.primary),{height:"40px",position:"relative",marginLeft:o?"30px":"28.5px",display:"flex",alignItems:"center",border:o?"none":`1.5px solid ${n.ketcher.outline.color}`,backgroundColor:r,borderRadius:n.ketcher.border.radius.regular,boxShadow:o?n.ketcher.shadow.regular:"none",padding:"5px 10px",color:t?"white":"black",gap:"8px",cursor:"pointer",outlineOffset:"1px",boxSizing:"border-box",":hover":{outline:n.ketcher.outline.small},":not(:last-child)":{":after":{content:'""',position:"absolute",right:"100%",bottom:"calc(50% - 1px)",borderLeft:n.ketcher.outline.medium,borderBottom:n.ketcher.outline.medium,height:"2px",width:"17px"}},":last-child":{":after":{content:'""',position:"absolute",right:"100%",bottom:"calc(50% - 1px)",borderLeft:n.ketcher.outline.medium,borderBottom:n.ketcher.outline.medium,borderRadius:"0 0 0 4px",height:"130px",width:"17px"}}}}),wi=g.A.div`
  display: flex;
  flex-direction: column;
`,Ai=(0,g.A)("span",{shouldForwardProp:e=>"selected"!==e})`
  font-size: ${({theme:e})=>e.ketcher.font.size.small};
  color: ${({selected:e,theme:t})=>e?t.ketcher.color.background.primary:t.ketcher.color.text.light};
  opacity: ${({selected:e})=>e?.4:1};
`,Ci=(0,g.A)("span",{shouldForwardProp:e=>"selected"!==e&&"empty"!==e})`
  margin-top: 1px;
  font-size: ${({theme:e})=>e.ketcher.font.size.medium};
  color: ${({selected:e,empty:t,theme:o})=>e?o.ketcher.color.button.text.primary:t?"#b4b9d6":o.ketcher.color.text.primary};
  opacity: ${({selected:e,empty:t})=>e&&t?.4:1};
`,Mi=g.A.div`
  position: relative;
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
`,ki=(0,g.A)(f.In,{shouldForwardProp:e=>"selected"!==e&&"empty"!==e})`
  fill: ${({selected:e,empty:t,theme:o})=>t?"none":e?o.ketcher.color.background.primary:o.ketcher.color.icon.grey};
  color: ${({selected:e,theme:t})=>e?t.ketcher.color.background.primary:t.ketcher.color.icon.grey};
`,Ii=(0,g.A)("div",{shouldForwardProp:e=>"selected"!==e&&"isEditMode"!==e})`
  position: relative;
  width: 60px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px;
  border-radius: 4px;
  box-shadow: 0 1px 2px 0 rgba(180, 185, 214, 0.6);
  cursor: pointer;
  background-color: ${({selected:e,theme:t})=>e?t.ketcher.color.button.primary.active:t.ketcher.color.background.primary};

  &:hover {
    outline: ${({theme:e})=>e.ketcher.outline.selected.small};
  }
`,Si=g.A.div`
  position: absolute;
  top: -35%;
  left: 50%;
  height: 15px;
  width: 2px;
  background-color: ${({theme:e})=>e.ketcher.outline.color};
`,Yi=(0,g.A)("p",{shouldForwardProp:e=>"selected"!==e&&"empty"!==e})`
  margin: 0;
  font-size: ${({theme:e})=>e.ketcher.font.size.small};
  font-weight: ${({theme:e})=>e.ketcher.font.weight.regular};
  color: ${({selected:e,empty:t,theme:o})=>e?o.ketcher.color.button.text.primary:t?"#b4b9d6":o.ketcher.color.text.primary};
  opacity: ${({selected:e,empty:t})=>e&&t?.4:1};
`,Li={[Ut.SUGARS]:"Sugar",[Ut.BASES]:"Base",[Ut.PHOSPHATES]:"Phosphate"};var Di=(0,i.memo)(({selected:e,empty:t,name:o})=>(0,n.Y)(Mi,{children:(0,n.Y)(ki,{selected:e,empty:t,name:o})}));const Ei=({groupName:e,iconName:t,monomerName:o,selected:r,onClick:i,testid:a,children:s})=>{const l=Dr(ln),d=!o;return(0,n.Y)(Ii,{selected:r,onClick:i,isEditMode:l,"data-testid":a,children:(0,n.FD)(n.FK,{children:[(0,n.Y)(Si,{}),(0,n.Y)(Di,{name:t,selected:r,empty:d}),(0,n.Y)(Yi,{selected:r,empty:d,children:o??Li[e]}),s]})})},Fi=({groupName:e,iconName:t,monomerName:o,selected:r,onClick:i,testid:a,children:s})=>{const l=Dr(ln),d=!o;return(0,n.Y)(vi,{selected:r,onClick:i,isEditMode:l,"data-testid":a,children:(0,n.FD)(n.FK,{children:[(0,n.Y)(Di,{name:t,selected:r,empty:d}),(0,n.FD)(wi,{children:[(0,n.Y)(Ai,{selected:r,children:Li[e]}),(0,n.Y)(Ci,{empty:d,selected:r,children:o??"Not selected"})]}),s]})})},Ni=e=>Rr()?(0,n.Y)(Ei,{...e,children:e.children}):(0,n.Y)(Fi,{...e,children:e.children}),Pi=g.A.div(e=>({display:"flex",flexDirection:"column",padding:"10px",backgroundColor:"#F7F9FA",borderRadius:"0 0 4px 4px","&.rna-editor-expanded--sequence-edit-mode":{padding:"8px",paddingTop:"10px",border:`2px ${e.theme.ketcher.color.editMode.sequenceInRNABuilder} solid`,borderTop:"none"}})),Bi=g.A.input`
  width: 100%;
  padding: 6px;
  border: none;
  border-radius: 4px;
  box-shadow: 0 1px 2px 0 rgba(180, 185, 214, 0.6);

  &:hover,
  &:focus {
    outline: ${({theme:e})=>e.ketcher.outline.selected.small};
  }
`,Ti=g.A.div`
  width: 100%;
  display: flex;
  flex-direction: ${({compact:e})=>e?"row":"column"};
  justify-content: ${({compact:e})=>e?"space-between":"flex-start"};
  gap: 8px;
  margin-top: 16px;
`;g.A.div({marginTop:"12px",display:"flex",alignItems:"center",justifyContent:"space-between",gap:"8px"}),g.A.span(({theme:e})=>({fontSize:e.ketcher.font.size.small,color:e.ketcher.color.text.light})),g.A.div({display:"flex",alignItems:"center",gap:"8px"}),g.A.button(({theme:e,selected:t})=>({minWidth:"40px",padding:"4px 8px",borderRadius:e.ketcher.border.radius.regular,border:t?e.ketcher.outline.selected.small:e.ketcher.outline.grey.small,color:t?e.ketcher.color.button.text.primary:e.ketcher.color.text.primary,backgroundColor:t?e.ketcher.color.button.primary.active:e.ketcher.color.background.primary,cursor:"pointer",":disabled":{opacity:.5,cursor:"not-allowed"}}));const Ri=g.A.div({marginTop:"16px",display:"flex",justifyContent:"space-evenly",alignItems:"center",gap:"8px"}),Oi=(0,g.A)(f.$n)(e=>({width:"100%",whiteSpace:"nowrap",fontSize:e.theme.ketcher.font.size.regular,backgroundColor:e.primary?e.theme.ketcher.color.button.primary.active:void 0,color:e.primary&&!e.disabled?e.theme.ketcher.color.button.text.primary:e.theme.ketcher.color.text.light,outline:e.primary&&!e.disabled?e.theme.ketcher.outline.selected.small:e.theme.ketcher.outline.grey.small})),zi=g.A.div(e=>({position:"relative",borderRadius:e.theme.ketcher.border.radius.regular,backgroundColor:e.theme.ketcher.color.background.primary,boxShadow:e.theme.ketcher.shadow.regular,cursor:"pointer",overflow:"hidden",padding:"0 6px 6px 6px",display:"flex",alignItems:"flex-end",height:"48px",outline:e.selected?e.theme.ketcher.outline.selected.medium:"none",":hover":{outline:e.selected?void 0:e.theme.ketcher.outline.small}})),$i=g.A.span(e=>({position:"absolute",top:"0",left:"0",width:"100%",height:"8px",backgroundColor:e.selected?e.theme.ketcher.outline.selected.color:e.theme.ketcher.outline.color})),ji=(0,g.A)(f.pd)({width:"100%","&:disabled":{background:"none",outline:"none",color:"inherit"}}),_i=g.A.div({overflow:"hidden",textOverflow:"ellipsis"}),Hi=e=>{const t=document.body.querySelector(e);t?.scrollIntoView()},qi=e=>{Hi(`[data-rna-preset-item-name="${e}"]`)},Vi=e=>{Hi(`[data-monomer-item-id="${e}"]`)},Wi=e=>0===e.size?"":1===e.size?[...e][0]:"[multiple]",Ui=e=>{if(!e?.length)return;const t={sugarLabel:new Set,baseLabel:new Set,phosphateLabel:new Set};for(const o of e)for(const e of["sugarLabel","baseLabel","phosphateLabel"])(o?.[e]||!o?.[e]&&o.type===c.WDE.Nucleoside&&!o.isNucleosideConnectedAndSelectedWithPhosphate)&&t[e].add(o?.[e]);return{Sugars:Wi(t.sugarLabel),Bases:Wi(t.baseLabel),Phosphates:Wi(t.phosphateLabel)}},Ki=e=>{e(Yn(null)),e(Fn(!1))},Gi=e=>{Ki(e)},Qi=(e,t,o=!0)=>{Ki(e),e(wn([])),t?.events.turnOffSequenceEditInRNABuilderMode.dispatch(o),"sequence-layout-mode"===t?.mode?.modeName&&t.mode.turnOffEditMode(o)},Zi=e=>e.filter(e=>e.type?e.type===c.WDE.Nucleotide||e.type===c.WDE.Nucleoside:!!e.node&&(e.node instanceof c.NxW||e.node instanceof c.yYC)).length;var Xi="RnaEditorExpanded-module_phosphatePositionIconWrapper__XHea5",Ji="RnaEditorExpanded-module_active__9PKEX",ea="RnaEditorExpanded-module_hover__0ZbV5",ta="RnaEditorExpanded-module_phosphatePositionIconWrapperOnPresetCard__-IbfN",oa="RnaEditorExpanded-module_phosphatePositionIconWrapperOnPresetCardActive__bAVbU",na="RnaEditorExpanded-module_phosphatePositionSelector__NE5RX",ra="RnaEditorExpanded-module_phosphatePositionTrigger__IpdSG",ia="RnaEditorExpanded-module_phosphatePositionOption__GO-S4",aa="RnaEditorExpanded-module_phosphatePositionOptionDisabled__oxZ6W";const sa=({isEditMode:e,onDuplicate:t})=>{const o=[{groupName:Ut.SUGARS,iconName:"sugar",testId:"rna-builder-slot--sugar"},{groupName:Ut.BASES,iconName:"base",testId:"rna-builder-slot--base"},{groupName:Ut.PHOSPHATES,iconName:"phosphate",testId:"rna-builder-slot--phosphate"}],a=(0,r.wA)(),s="sequence-layout-mode"===Er(),l=Dr(en),d=Dr(hn),p=Dr(Xo),u=Dr(kt),h=Dr(xn),m=Dr(an),[g,b]=(0,i.useState)(l),[x,y]=(0,i.useState)(l?.connections?.length?(0,c.uVP)(l):void 0),v=e=>{if(!e?.phosphate)return;const{is3PrimeAvailable:t,is5PrimeAvailable:o}=Wo(e);if("left"===x&&o)return"left";if("right"===x&&t)return"right";if(e.connections?.length){const n=(0,c.uVP)(e);if("left"===n&&o||"right"===n&&t)return n}return s&&t?"right":o&&!t?"left":t&&!o?"right":void 0},w=Dr(tn),A=Dr(on),C=Dr(It),[M,k]=(0,i.useState)(!1),[I,S]=(0,i.useState)(Ui(w)),Y=v(g),{is3PrimeAvailable:L,is5PrimeAvailable:D}=Wo(g||{}),E=Boolean(g?.phosphate)&&L&&D,F=!Y&&E,N=F?"Before saving you must choose the position of the phosphate.":"",P={left:"Sugar must have R1, and phosphate must have R2.",right:"Sugar must have R2, and phosphate must have R1."};(0,i.useEffect)(()=>{b(l),y(l?.connections?.length?(0,c.uVP)(l):void 0)},[l]),(0,i.useEffect)(()=>{w&&(1===Zi(w)&&a(An((e=>{const t=Ui(e);return`${t?.Sugars}(${t?.Bases})${t?.Phosphates??""}`})(w))),S(Ui(w)))},[a,w]),(0,i.useEffect)(()=>{if(p!==Ko.Presets&&e)if(C&&m){const e=`${Qo[m.groupName]}Label`,t=w.map(t=>t.isNucleosideConnectedAndSelectedWithPhosphate&&"phosphateLabel"===e||t.type===c.WDE.Phosphate&&("sugarLabel"===e||"baseLabel"===e)?t:{...t,[e]:m.groupItem.label,rnaBaseMonomerItem:"Bases"===m.groupName?m.groupItem:t.rnaBaseMonomerItem});k(!0),a(wn(t))}else{const e=(()=>{if(m){const e=Qo[m.groupName],t={...g,[e]:m.groupItem};return b(t),t}return g})();let t=g?.name;e.editedName||(t=dn(e)),b({...e,name:t})}},[m?.groupItem,C]);const B=t=>()=>{const o=rn(g,t);o&&!s&&u?.events.selectMonomer.dispatch(o),g[Qo[t]]&&a(In(so(g[Qo[t]]))),a(kn(t)),a(Sn({rnaPreset:g,isEditMode:e,selectedPhosphatePosition:x}));let n="";if(C&&w.length>0){const e=w[0].baseLabel;if(e&&w.every(t=>t.baseLabel===e)){const e=w[0].rnaBaseMonomerItem;e&&(n=so(e),a(In(n)))}}setTimeout(()=>((e,t)=>{if(e===Ko.Presets)return qi(g?.name),void(g&&u?.events.selectPreset.dispatch(g));const o=g[Qo[e]];o?Vi(so(o)):t&&Vi(t)})(t,n),100)},T=t=>{if(e){const e=t.target.value;b({...g,name:e.trim(),editedName:!0})}},R=(e,t=!1,o=!1)=>(0,n.Y)("div",{className:(0,V.A)(Xi,t&&Ji,o&&ea),children:"left"===e?(0,n.Y)(f.In,{name:"preset-left-phosphate"}):(0,n.Y)(f.In,{name:"preset-right-phosphate"})}),O=e=>"left"===e?"Phosphate on the left":"Phosphate on the right",z=(t,o)=>{let r;return r=o?P[t]:x===t?O(t):`Switch to ${t}`,(0,n.Y)(j.A,{title:r,children:(0,n.Y)("span",{children:(0,n.Y)("button",{type:"button",className:(0,V.A)(ia,o&&aa),disabled:o,onClick:()=>{(t=>{y(t),a(Sn({rnaPreset:g,isEditMode:e,selectedPhosphatePosition:t}))})(t)},children:R(t,!1,!0)})})},t)},$=t=>{const o=C||!D&&!L,r=C?"right":t??x??"right",i=!C&&p===Ut.PHOSPHATES,a=!e||!i;return(0,n.FD)("div",{className:(0,V.A)(ta,i&&!o&&oa),children:[(0,n.Y)(j.A,{title:a?O(r):"",children:(0,n.Y)("span",{children:(0,n.Y)("button",{type:"button",className:ra,disabled:o,"aria-label":"Select phosphate position",children:R(r,i)})})}),o||a?null:(0,n.FD)("div",{className:na,children:[z("left",!D),z("right",!L)]})]})},_=()=>{Zi(w)>1?a(Yo("updateSequenceInRNABuilder")):(u?.events.modifySequenceInRnaBuilder.dispatch(w),Qi(a,u))},H=()=>{const e=g?.name;if(!e)return;if(g.editedName&&(t=e,!Uo.test(t)))return void a(Bn(e));var t;const o=v(g);if(g?.phosphate&&!o)return;const n={...g,connections:(0,c.Qkd)(g,o)},r=h.find(t=>t.name===e);r&&l.nameInList!==r.name?a(Nn(e)):(a(Ln(n)),a(vn(n)),s||u?.events.selectPreset.dispatch(n),setTimeout(()=>{qi(e)},0),Gi(a))},q=()=>{C?Qi(a,u,!1):d&&h.length>0?(Gi(a),a(vn(h[0]))):(b(l),y(l?.connections?.length?(0,c.uVP)(l):void 0),Gi(a))},W=()=>{a(Fn(!0))},U=e=>{if(m&&m.groupName===e)return m.groupItem.label;return(rn(g,e)?.label??rn(g,e)?.props.MonomerName)||void 0},K=e=>I?I[e]:"";let G;(0,i.useEffect)(()=>{const e=e=>{"Escape"===e.key?(q(),e.preventDefault(),e.stopPropagation(),e.stopImmediatePropagation()):"Enter"===e.key&&(C?_():u?.events.startNewSequence.dispatch({}),e.preventDefault(),e.stopPropagation())};return u?.events.keyDown.add(e),()=>{u?.events.keyDown.remove(e)}},[u,w,C]),(0,i.useEffect)(()=>{if(!C)return;const e=()=>q();return u?.events.cancelSequenceEditInRNABuilderMode.add(e),()=>{u?.events.cancelSequenceEditInRNABuilderMode.remove(e)}},[u,C]);const Q=!sn(g)||F;G=d&&!C?(0,n.Y)(j.A,{title:N,children:(0,n.Y)(Oi,{disabled:Q,primary:!0,"data-testid":"add-to-presets-btn",onClick:H,children:"Add to Presets"})}):e?(0,n.Y)(j.A,{title:N,children:(0,n.Y)(Oi,{primary:!0,disabled:C?!M:Q,"data-testid":"save-btn",onClick:C?_:H,children:C?"Update":"Save"})}):(0,n.Y)(Oi,{"data-testid":"edit-btn",onClick:W,disabled:l.default,children:"Edit"});const Z=Rr();return(0,n.FD)(Pi,{"data-testid":"rna-editor-expanded",className:(0,V.A)(C&&"rna-editor-expanded--sequence-edit-mode"),children:[Z?(0,n.Y)(Bi,{value:C?A:g?.name,placeholder:"Name your structure","data-testid":"name-your-structure-editbox",disabled:C,onChange:T}):(0,n.FD)(zi,{selected:p===Ko.Presets,onClick:()=>B(Ko.Presets),children:[e?(0,n.Y)(ji,{value:C?A:g?.name,placeholder:"Name your structure","data-testid":"name-your-structure-editbox",disabled:C,onChange:T}):(0,n.Y)(_i,{children:g?.name}),(0,n.Y)($i,{selected:p===Ko.Presets})]}),(0,n.Y)(Ti,{compact:Z,children:o.map(({groupName:e,iconName:t,testId:o})=>{const r=e===Ut.PHOSPHATES;return(0,n.Y)(Ni,{selected:p===e,groupName:e,monomerName:C?K(e):U(e),iconName:t,testid:o,onClick:B(e),children:r?$(Y):null},e)})}),(0,n.FD)(Ri,{children:[e?(0,n.Y)(Oi,{"data-testid":"cancel-btn",onClick:q,children:"Cancel"}):(0,n.Y)(Oi,{"data-testid":"duplicate-btn",disabled:!sn(g),onClick:()=>t(g),children:"Duplicate and Edit"}),G]})]})},la=g.A.div`
  padding: 8px;
`,da=g.A.button`
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px;
  background-color: ${e=>e.theme.ketcher.color.background.primary};
  font-weight: ${e=>e.theme.ketcher.font.weight.regular};
  font-size: ${e=>e.theme.ketcher.font.size.regular};
  border: none;
  border-radius: 4px;
  text-transform: none;
  cursor: pointer;

  &.styled-header--sequence-edit-mode {
    background-color: ${e=>e.theme.ketcher.color.editMode.sequenceInRNABuilder};
  }

  &.styled-header--expanded,
  &.styled-header--active-preset {
    border-radius: 4px 4px 0 0;
  }
`,ca=(0,g.A)(f.In,{shouldForwardProp:e=>"expanded"!==e})`
  height: 16px;
  width: 16px;
  transform: ${e=>e.expanded?"rotate(180deg)":"none"};
`,pa=({duplicatePreset:e})=>{const t=Dr(en),o=Dr(ln),r=Dr(It),a=dn(t),s=Lr(),l=Boolean(t?.name)||o,[d,c]=(0,i.useState)(l),[p,u]=(0,i.useState)(t);t!==p&&(u(t),t&&l&&c(!0)),(0,i.useEffect)(()=>{t||(s(En()),s(kn(Ko.Presets)))},[t,s]),(0,i.useEffect)(()=>{s(Sn({rnaPreset:t,isEditMode:o}))},[o,s,t]);return(0,n.FD)(la,{"data-testid":"rna-editor",children:[(0,n.FD)(da,{className:(0,V.A)(r&&"styled-header--sequence-edit-mode",d&&"styled-header--expanded",t?.name&&"styled-header--active-preset"),onClick:()=>{c(!d),t?.nameInList||s(Fn(!0))},"data-testid":"rna-builder-expand-button",children:["RNA Builder",(0,n.Y)(ca,{expanded:d,name:"chevron"})]}),t&&(d?(0,n.Y)(sa,{isEditMode:o,onDuplicate:e}):(0,n.Y)(yi,{name:t.name,fullName:a}))]})},ua=g.A.div({height:"100%",width:"100%",display:"flex",flexDirection:"column",justifyContent:"space-between"}),ha=({ketcher:e})=>a.AH`
  scrollbar-width: thin;
  scrollbar-color: ${e.color.scroll.regular} ${e.color.scroll.inactive};
  &::-webkit-scrollbar {
    width: 4px;
    height: 4px;
    background-color: ${e.color.scroll.inactive};
    border-radius: 2px;
    -webkit-border-radius: 2px;
  }
  &::-webkit-scrollbar-thumb {
    background-color: ${e.color.scroll.regular};
    border-radius: 2px;
    -webkit-border-radius: 2px;
  }
  &::-webkit-scrollbar-thumb:active {
    background-color: ${e.color.scroll.regular};
  }
`;var ma="Modal-module_expandButton__J1QYl";const ga=(0,g.A)(w.A)`
  .MuiPaper-root {
    min-width: 304px;
  }
`,fa=(0,g.A)(A.A)(({theme:e,hideborder:t})=>({padding:"2px 4px 2px 12px",display:"flex",alignItems:"center",justifyContent:"space-between",fontFamily:`${e.ketcher.font.family.inter}`,fontSize:`${e.ketcher.font.size.medium}`,fontWeight:500,textTransform:"capitalize",borderBottom:t?"none":"1px solid rgba(202, 211, 221, 1)"})),ba=g.A.div({marginRight:"10px",fontSize:"14px"}),xa=(0,g.A)(f.In)({width:"16px",height:"16px",color:"rgba(51, 51, 51, 1)"}),ya=(0,g.A)(C.A)`
  padding: 0;
  font-size: ${({theme:e})=>e.ketcher.font.size.medium};
  line-height: 17px;
  color: #000000;
  ${({theme:e})=>ha(e)};
`,va=(0,g.A)(M.A)`
  height: 52px;
  margin: 0;
  padding: 0 12px;
  border-top: ${({theme:e,withborder:t})=>"true"===t?e.ketcher.border.small:"none"};
  justify-content: flex-end;

  .MuiButtonBase-root {
    border-radius: 4px;
    font-size: ${({theme:e})=>e.ketcher.font.size.regular};
  }
`,wa=({children:e,title:t,isOpen:o,showCloseButton:r=!0,showExpandButton:a=!1,onClose:l,className:d,modalWidth:c,expanded:p=!1,setExpanded:u=kr,testId:h,hideHeaderBorder:m})=>{const g=(0,s.u)(),b=(0,i.useMemo)(()=>({...h?{testid:h}:{},"data-testid":h,style:{background:g.ketcher.color.background.primary,borderRadius:"8px",color:g.ketcher.color.text.primary,...a&&{margin:"auto",width:p?"100%":c,height:p?"100%":void 0,maxWidth:"calc(min(1280px, 100%))",maxHeight:"calc(min(980px, 100%))"}}}),[h,g.ketcher.color.background.primary,g.ketcher.color.text.primary,a,p,c]),x=(0,i.useMemo)(()=>({style:{background:g.ketcher.color.background.overlay,opacity:.4}}),[g.ketcher.color.background.overlay]),y={Content:null,Footer:null};return i.Children.forEach(e,e=>{e.type===ya?y.Content=e:e.type===va&&(y.Footer=e)}),(0,n.FD)(ga,{BackdropProps:x,PaperProps:b,open:o,onClose:l,container:document.querySelector(f.tS),disableEscapeKeyDown:!r,className:d,sx:{padding:"24px"},children:[t||r||a?(0,n.FD)(fa,{hideborder:m,children:[(0,n.Y)(ba,{children:t}),(0,n.FD)("span",{children:[a&&(0,n.Y)(k.A,{title:p?"Minimize window":"Expand window","data-testid":"expand-window-button",className:ma,onClick:()=>{u(!p)},children:(0,n.Y)(xa,{name:p?"minimize-expansion":"expand"})}),r&&(0,n.Y)(k.A,{title:"Close window",onClick:l,"data-testid":"close-window-button",children:(0,n.Y)(xa,{name:"close"})})]})]}):"",y.Content,y.Footer]})};wa.Content=ya,wa.Footer=va;const Aa=(0,a.AH)({padding:"5px 8px",borderRadius:"2px",textTransform:"none",lineHeight:"14px",fontSize:"12px",textAlign:"center","&.MuiButtonBase-root":{width:"unset",minWidth:"70px"}}),Ca=(0,g.A)(W.A)(({theme:e})=>({backgroundColor:e.ketcher.color.button.primary.active,border:`1px solid ${e.ketcher.color.button.primary.active}`,color:"rgb(245, 245, 245)",fontWeight:e.ketcher.font.weight.regular,width:"62px",height:"24px","&:hover":{backgroundColor:e.ketcher.color.button.primary.hover},"&:disabled":{background:e.ketcher.color.button.primary.disabled,border:"1px solid transparent",opacity:.4}}),Aa),Ma=(0,g.A)(W.A)(({theme:e})=>({backgroundColor:"transparent",border:`1px solid ${e.ketcher.color.button.secondary.active}`,color:e.ketcher.color.button.secondary.active,fontWeight:e.ketcher.font.weight.regular,width:"72px",height:"24px","&:hover":{border:`1px solid ${e.ketcher.color.button.secondary.hover}`,color:e.ketcher.color.button.secondary.hover},"&:disabled":{border:`1px solid ${e.ketcher.color.button.secondary.disabled}`,color:e.ketcher.color.button.secondary.disabled},"&:clicked":{border:`1px solid ${e.ketcher.color.button.secondary.clicked}`,color:e.ketcher.color.button.secondary.clicked}}),Aa),ka=({label:e,clickHandler:t,children:o,styleType:r,...i})=>"secondary"===r?(0,n.Y)(Ma,{onClick:t,title:i.title||e,...i,children:o||e}):(0,n.Y)(Ca,{onClick:t,title:i.title||e,...i,children:o||e}),Ia=g.A.div`
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  overflow: hidden;
  height: 100%;
`,Sa=(0,g.A)(f.nD)`
  min-height: 32px;
`,Ya=g.A.div`
  flex-grow: 2;
  display: flex;
  flex-direction: column;
  min-height: 0;

  /*
   * The phosphate filter popup is positioned absolutely relative to the
   * preset toolbar. The Accordion library wraps content in a container with
   * \`overflow: hidden\` and a details container with \`overflow-y: auto\`,
   * which clip the popup vertically when only a few presets match (so the
   * details container is short).
   *
   * Override these so the popup can escape vertically. Vertical scrolling for
   * long preset lists is moved to PresetsScrollArea below, which sits
   * **after** the toolbar — the toolbar (and its popup) is therefore outside
   * the scroll area and never clipped.
   */
  > div {
    overflow: visible;
    display: flex;
    flex-direction: column;
    min-height: 0;

    /* Accordion's inner details container (sibling of the summary). */
    > div + div {
      overflow: visible;
      display: flex;
      flex-direction: column;
      min-height: 0;
      flex: 1 1 auto;
    }
  }
`,La=g.A.div`
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
`,Da=g.A.div`
  position: relative;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  justify-content: start;
  padding: ${({compact:e})=>e?"4px":"8px"};
  /* Fill the accordion's details container so PresetsScrollArea can take
   * the remaining vertical space. The tabs view uses \`compact\` and is
   * sized by its own ancestors. */
  ${({compact:e})=>e?"":"flex: 1 1 auto; min-height: 0;"}
`,Ea=g.A.div`
  flex-grow: 1;
  min-height: 0;
  background-color: #f7f9fa;
  border-radius: 4px;
  margin: 4px 8px;
  padding: 4px;

  &.first-tab {
    border-radius: 0 4px 4px 4px;
  }

  &.last-tab {
    border-radius: 4px 0 4px 4px;
  }
`,Fa=g.A.div`
  height: 100%;
  background-color: ${({theme:e})=>e.ketcher.color.tab.content};
  border-radius: 2px;
  overflow: auto;
`,Na=(0,g.A)(f.$n)`
  background-color: ${({theme:e})=>e.ketcher.color.button.transparent.active};
  color: ${({theme:e})=>e.ketcher.color.text.light};
  border-color: ${({theme:e})=>e.ketcher.color.text.light};
`,Pa=g.A.div`
  position: relative;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  width: 100%;
`,Ba=(0,g.A)(Na)`
  height: 24px;
  border-radius: 4px;
  font-size: 12px;
`,Ta=g.A.button`
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  margin-left: auto;
  background-color: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  color: #333333;

  > svg {
    width: 16px;
    height: 16px;
  }

  /* Indicator dot shown in the top-right corner of the filter icon when the
   * current filter state differs from the default (all options off). */
  ${({hasIndicator:e,theme:t})=>e?`\n    &::after {\n      content: '';\n      position: absolute;\n      top: 5px;\n      right: 5px;\n      width: 6px;\n      height: 6px;\n      border-radius: 50%;\n      border: 1px solid #E1E5EA;\n      background-color: ${t.ketcher.color.button.primary.active};\n    }\n  `:""}
`,Ra=g.A.div`
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  min-width: 160px;
  padding: 4px;
  background-color: #ffffff;
  border: 1px solid #cad3dd;
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
  font-size: 12px;
  color: ${({theme:e})=>e.ketcher.color.text.primary};
`,Oa=g.A.label`
  display: flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  user-select: none;
  margin-left: 6px;
  margin-top: 10px;
`,za=g.A.input`
  position: absolute;
  opacity: 0;
  cursor: pointer;

  & + span {
    display: inline-block;
    width: 16px;
    height: 16px;
    vertical-align: middle;
    background-image: url("data:image/svg+xml,%3Csvg width='16' height='16' viewBox='0 0 16 16' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Crect x='0.5' y='0.5' width='15' height='15' rx='3.5' fill='white' stroke='%23B4B9D6'/%3E%3C/svg%3E%0A");
    background-repeat: no-repeat;
    background-size: 100%;
  }

  &:checked + span {
    background-image: url("data:image/svg+xml,%3Csvg width='16' height='16' viewBox='0 0 16 16' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Crect width='16' height='16' rx='4' fill='%23167782'/%3E%3Cpath d='M6.33711 11.8079L2.42578 7.63124L3.39911 6.71991L6.36845 9.89124L12.6171 3.64258L13.5598 4.58524L6.33711 11.8079Z' fill='white'/%3E%3C/svg%3E%0A");
  }
`,$a=g.A.div`
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0.4px;
  margin: 5px 0 5px 2px;
  color: ${({theme:e})=>e.ketcher.color.text.primary};
`,ja=g.A.hr`
  width: 100%;
  height: 1px;
  margin: 8px 0 4px;
  border: none;
  background-color: #e1e5ea;
`,_a=g.A.div`
  display: flex;
  flex-direction: row;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
`,Ha=(0,g.A)(ka)`
  &.MuiButtonBase-root {
    width: auto;
    min-width: unset;
    padding-left: 7px;
    padding-right: 7px;
    border-radius: 4px;
  }
`,qa=(0,g.A)(Ha)`
  &.MuiButtonBase-root {
    border: none;
    margin-right: auto;
    padding-left: 0;
    margin-left: 6px;
    color: #167782;
  }
`,Va=g.A.div`
  width: 100%;
  height: 100%;
  background-color: #eff2f594;
  position: absolute;
  top: 0;
  left: 0;
`,Wa=g.A.div`
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px 0;
`,Ua=g.A.div`
  position: relative;
  display: flex;
  align-items: center;

  &.rna-tab--selected {
    & button {
      border-radius: 4px 4px 0 0;
      background-color: #f7f9fa;
    }

    &::after {
      content: '';
      position: absolute;
      bottom: -4px;
      left: 0;
      right: 0;
      height: 8px;
      background-color: #f7f9fa;
    }
  }
`,Ka=(0,g.A)(v.A)`
  height: 24px;
  min-height: 24px;
  min-width: 24px;
  ${({selected:e})=>e?"min-width: 104px;":""}
  display: flex;
  flex-direction: row;
  gap: 4px;
  align-items: center;
  justify-content: center;
  padding: 4px;
  ${({selected:e})=>e?"margin-top: -8px;":""}
  font-weight: 400;
  font-size: 10px;
  border-radius: 4px;
  background-color: white;
  opacity: ${({selected:e})=>e?1:.6};
  text-transform: none;

  &:hover {
    background-color: #f3f8f9;
  }

  > svg {
    height: 16px;
    width: 16px;
    color: #b4b9d6;

    &.MuiTab-iconWrapper {
      margin: 0;
    }
  }
`,Ga=e=>{const t=Dr(Ao),o=Dr(yn),n=yo(t,e),r=Co(n),a=(e=>e.filter(e=>!e.isAmbiguous&&(e.props?.MonomerClass===c.IPc.RNA||e.props?.MonomerClass===c.IPc.DNA)))(t),s=Co(a);return(0,i.useMemo)(()=>[{groupName:Ko.Presets,iconName:"preset",groups:[{groupItems:o}]},{groupName:Ut.SUGARS,iconName:"sugar",groups:r.map(e=>({...e,groupItems:e.groupItems.filter(e=>e.props?.MonomerClass===c.IPc.Sugar)})).filter(e=>e.groupItems.length)},{groupName:Ut.BASES,iconName:"base",groups:r.map(e=>({...e,groupItems:e.groupItems.filter(e=>e.props?.MonomerClass===c.IPc.Base)})).filter(e=>e.groupItems.length)},{groupName:Ut.PHOSPHATES,iconName:"phosphate",groups:r.map(e=>({...e,groupItems:e.groupItems.filter(e=>e.props?.MonomerClass===c.IPc.Phosphate)})).filter(e=>e.groupItems.length)},{groupName:Ut.NUCLEOTIDES,iconName:"nucleotide",groups:s}],[r,o,s])},Qa={fivePrime:!1,threePrime:!1,noPhosphate:!1},Za=({onClose:e})=>{const t=(0,r.wA)(),o=Dr(gn),[a,s]=(0,i.useState)(o),l=(0,i.useRef)(null);(0,i.useEffect)(()=>{const t=t=>{const o=t.target;l.current&&o&&l.current.contains(o)||o instanceof Element&&o.closest('[data-testid="preset-filter-button"]')||e()},o=t=>{"Escape"===t.key&&e()};return document.addEventListener("mousedown",t),document.addEventListener("keydown",o),()=>{document.removeEventListener("mousedown",t),document.removeEventListener("keydown",o)}},[e]);const d=e=>()=>{s(t=>({...t,[e]:!t[e]}))};return(0,n.FD)(Ra,{ref:l,"data-testid":"preset-phosphate-filter-popup",onClick:e=>e.stopPropagation(),children:[(0,n.Y)($a,{children:"Filter"}),(0,n.FD)(Oa,{children:[(0,n.Y)(za,{type:"checkbox",checked:a.fivePrime,onChange:d("fivePrime"),"data-testid":"preset-filter-5-phosphate"}),(0,n.Y)("span",{})," 5'-phosphate"]}),(0,n.FD)(Oa,{children:[(0,n.Y)(za,{type:"checkbox",checked:a.threePrime,onChange:d("threePrime"),"data-testid":"preset-filter-3-phosphate"}),(0,n.Y)("span",{})," 3'-phosphate"]}),(0,n.FD)(Oa,{children:[(0,n.Y)(za,{type:"checkbox",checked:a.noPhosphate,onChange:d("noPhosphate"),"data-testid":"preset-filter-no-phosphate"}),(0,n.Y)("span",{})," No phosphate group"]}),(0,n.Y)(ja,{}),(0,n.FD)(_a,{children:[(0,n.Y)(qa,{styleType:"secondary",label:"Reset all",clickHandler:()=>{s(Qa),t(zn(Qa)),e()},"data-testid":"preset-filter-reset"}),(0,n.Y)(Ha,{label:"Set",clickHandler:()=>{t(zn(a)),e()},"data-testid":"preset-filter-set"})]})]})};var Xa=(0,i.memo)(({activeRnaBuilderItem:e,groupsData:t,onNewPresetClick:o,onSelectItem:a,duplicatePreset:s,editPreset:l,libraryName:d})=>{const c=(0,r.wA)(),p=Dr(yn),u=Dr(Ao),h=Dr(ln),m=Dr(hn),g=Dr(bn),b=Dr(gn),x=Boolean(b?.fivePrime||b?.threePrime||b?.noPhosphate),[y,v]=(0,i.useState)(!1);return(0,n.FD)(n.FK,{children:[(0,n.Y)(Wa,{children:t.map(t=>{const{groupName:o,groups:r,iconName:i}=t,a=o===e,s=[...r,...vo(u,o)].reduce((e,t)=>e+(t.groupItems.length||0),0),l=a?`${o} (${s})`:null;return(0,n.Y)(Ua,{className:(0,V.A)(a&&"rna-tab--selected"),children:(0,n.Y)(Ka,{label:l,title:o,selected:a,icon:(0,n.Y)(f.In,{name:i}),onClick:()=>c(kn(o)),"data-testid":`summary-${o}`})},o)})}),t.map(t=>{const{groupName:r,groups:i}=t;if(r!==e)return null;const c=vo(u,r),b=r===Ko.Presets?(0,n.FD)(Da,{compact:!0,children:[(0,n.FD)(Pa,{children:[(0,n.Y)(Ba,{onClick:o,"data-testid":"new-preset-button",children:"Add new"}),(0,n.Y)(Ta,{type:"button",active:y,hasIndicator:x,onClick:e=>{e.stopPropagation(),v(e=>!e)},"aria-label":"Filter presets by phosphate position","data-testid":"preset-filter-button",children:(0,n.Y)(f.In,{name:"filter"})}),y&&(0,n.Y)(Za,{onClose:()=>v(!1)})]}),(0,n.Y)(mi,{duplicatePreset:s,editPreset:l,presets:p}),h&&!m&&(0,n.Y)(Va,{})]}):(0,n.Y)(Da,{compact:!0,children:(0,n.FD)(n.FK,{children:[i.map(({groupItems:e,groupTitle:t})=>(0,n.Y)(oi,{title:[Ut.BASES,Ut.NUCLEOTIDES].includes(r)||i.length>1?t:void 0,groupName:r,items:e,selectedMonomerUniqueKey:g,onItemClick:e=>a(e,r)},t)),c.map(({groupTitle:e,groupItems:t})=>(0,n.Y)(oi,{title:e,items:t,groupName:r,libraryName:d,selectedMonomerUniqueKey:g,onItemClick:e=>a(e,r)},e))]})}),w=e===Ko.Presets,A=e===Ut.NUCLEOTIDES;return(0,n.Y)(Ea,{className:(0,V.A)(w&&"first-tab",A&&"last-tab"),children:(0,n.Y)(Fa,{children:b})},r)})]})});const Ja=({groupName:e,quantity:t,expanded:o,iconName:r})=>(0,n.FD)(ii,{"data-testid":`summary-${e}`,children:[(0,n.Y)(si,{name:r}),(0,n.FD)(ai,{children:[e," (",t,")"]}),(0,n.Y)(si,{name:"chevron",expanded:o})]});var es=(0,i.memo)(({activeRnaBuilderItem:e,groupsData:t,newPreset:o,onNewPresetClick:a,onSelectItem:s,duplicatePreset:l,editPreset:d,libraryName:c})=>{const p=(0,r.wA)(),u=Dr(yn),h=Dr(Ao),m=Dr(ln),g=Dr(hn),b=Dr(bn),x=Dr(gn),y=Boolean(x?.fivePrime||x?.threePrime||x?.noPhosphate),[v,w]=(0,i.useState)(e),[A,C]=(0,i.useState)(e),[M,k]=(0,i.useState)(!1);e!==A&&(C(e),w(e));const I=e=>{v===e?w(null):(w(e),p(Sn({rnaPreset:o,isEditMode:m})))};return(0,n.Y)(n.FK,{children:t.map(e=>{const t=v===e.groupName,o=vo(h,e.groupName),r=[...e.groups,...o].reduce((e,t)=>e+(t.groupItems.length||0),0),i=(0,n.Y)(Ja,{iconName:e.iconName,groupName:e.groupName,quantity:r,expanded:t}),p=e.groupName===Ko.Presets?(0,n.FD)(Da,{children:[(0,n.FD)(Pa,{children:[(0,n.Y)(Ba,{onClick:a,"data-testid":"new-preset-button",children:"Add new"}),(0,n.Y)(Ta,{type:"button",active:M,hasIndicator:y,onClick:e=>{e.stopPropagation(),k(e=>!e)},"aria-label":"Filter presets by phosphate position","data-testid":"preset-filter-button",children:(0,n.Y)(f.In,{name:"filter"})}),M&&(0,n.Y)(Za,{onClose:()=>k(!1)})]}),(0,n.Y)(La,{children:(0,n.Y)(mi,{duplicatePreset:l,editPreset:d,presets:u})}),m&&!g&&(0,n.Y)(Va,{})]}):(0,n.Y)(Da,{children:(0,n.FD)(n.FK,{children:[e.groups.map(({groupItems:t,groupTitle:o})=>{const r=[Ut.BASES,Ut.NUCLEOTIDES,Ut.PEPTIDES].includes(e.groupName);return(0,n.Y)(oi,{title:r?o:void 0,groupName:e.groupName,items:t,selectedMonomerUniqueKey:b,onItemClick:t=>s(t,e.groupName)},o)}),o.map(t=>(0,n.Y)(oi,{title:t.groupTitle,items:t.groupItems,groupName:e.groupName,libraryName:c,selectedMonomerUniqueKey:b,onItemClick:t=>s(t,e.groupName)},t.groupTitle))]})});return e.groupName===Ko.Presets&&t?(0,n.Y)(Ya,{children:(0,n.Y)(Sa,{"data-testid":"styled-accordion",dataTestIdDetails:`rna-accordion-details-${e.groupName}`,summary:i,details:p,expanded:t,onSummaryClick:()=>I(e.groupName)})},e.groupName):(0,n.Y)(Sa,{"data-testid":"styled-accordion",dataTestIdDetails:`rna-accordion-details-${e.groupName}`,summary:i,details:p,expanded:t,onSummaryClick:()=>I(e.groupName)},e.groupName)})})});const ts=({view:e,libraryName:t,duplicatePreset:o,editPreset:a})=>{const s=(0,r.wA)(),l=Dr(Xo),d=Dr(en),p=Dr(ln),u=Dr(kt),h=Dr(It),[m,g]=(0,i.useState)(d),[f,b]=(0,i.useState)({activePreset:d,isEditMode:p});f.activePreset===d&&f.isEditMode===p||(b({activePreset:d,isEditMode:p}),g(d)),(0,i.useEffect)(()=>{p||s(kn(Ko.Presets))},[p,s]);const x=Ga(t),y=(0,i.useCallback)(()=>{s(En()),s(kn(Ko.Presets)),s(Fn(!0))},[s]),v=(0,i.useCallback)((e,t)=>{if(p&&s(In(so(e))),h||p||u?.events.selectMonomer.dispatch(e),!p)return;const o=(0,c.eoH)(e)?e.monomers[0].monomerItem.props.MonomerClass?.toLowerCase():e.props.MonomerClass?.toLowerCase(),n={...m,[o]:e};g(n),s(Yn({groupName:t,groupItem:e})),s(kn(t))},[s,u,p,h,m]);return(0,n.Y)(Ia,{"data-testid":"rna-accordion",children:"tabs"===e?(0,n.Y)(Xa,{activeRnaBuilderItem:l,groupsData:x,onNewPresetClick:y,onSelectItem:v,libraryName:t,editPreset:a,duplicatePreset:o}):(0,n.Y)(es,{activeRnaBuilderItem:l,groupsData:x,newPreset:m,onNewPresetClick:y,onSelectItem:v,libraryName:t,editPreset:a,duplicatePreset:o})})},os=({libraryName:e,duplicatePreset:t,editPreset:o})=>{const r=Lr(),i=Dr(cn),a=Dr(pn),s=Dr(un),l=Rr(),d=()=>{i.length>0&&r(Nn("")),a.length>0&&r(Pn("")),s.length>0&&r(Bn(""))};return(0,n.FD)(ua,{children:[(0,n.Y)(pa,{duplicatePreset:t}),(0,n.Y)(ts,{libraryName:e,duplicatePreset:t,editPreset:o,view:l?"tabs":"accordion"}),(0,n.FD)(wa,{isOpen:Boolean(i)||Boolean(a)||Boolean(s),title:"Error Message",onClose:d,children:[(0,n.Y)(wa.Content,{children:(0,n.FD)("div",{style:{padding:"12px"},children:[i&&`Preset with name "${i}" already exists. Please choose another name.`,a&&`Preset with name "${a}" can't be used. Because it is impossible to establish bonds between monomers. Edit it's structure or choose another one.`,s&&"The preset code must consist only of uppercase and lowercase letters, numbers, hyphens (-), underscores (_), and asterisks (*)."]})}),(0,n.Y)(wa.Footer,{children:(0,n.Y)(Na,{onClick:d,children:"Close"})})]})]})},ns="_Copy",rs=({toggleLibraryVisibility:e})=>{const t=Lr(),o=Dr(Mo),r=Dr(xn);(0,i.useEffect)(()=>{t(fo(""))},[t]);const a=(0,i.useCallback)(e=>{t(fo(e.target.value))},[t]),s=(0,i.useCallback)(e=>{let o,n=`${e?.name}${ns}`;do{o=r.find(e=>e.name===n),o&&(n+=ns)}while(o);if(o)return void t(Nn(n));const i=o?`${n}${ns}`:n,a={...e,name:i,nameInList:i,default:!1,favorite:!1};t(vn(a)),t(Fn(!0)),qi(e?.name)},[t,r]),l=(0,i.useCallback)(e=>{t(vn(e)),t(Fn(!0))},[t]),d=(0,i.useMemo)(()=>((e,t)=>[{caption:"\u2605",tooltip:"Favorites",component:gi,testId:"FAVORITES-TAB",props:{libraryName:Ht,duplicatePreset:e,editPreset:t}},{caption:"Peptides",component:gi,testId:"PEPTIDES-TAB",props:{libraryName:qt}},{caption:"RNA",testId:"RNA-TAB",component:os,props:{libraryName:Wt,duplicatePreset:e,editPreset:t}},{caption:"CHEM",component:gi,testId:"CHEM-TAB",props:{libraryName:Vt}}])(s,l),[s,l]),c=(0,i.useCallback)((e,o)=>{t(bo(o))},[t]);return(0,n.FD)(Gn,{"data-testid":"monomer-library",children:[(0,n.FD)(Qn,{children:[(0,n.FD)(Zn,{children:[(0,n.Y)(Jn,{name:"search"}),(0,n.Y)(er,{type:"search","data-testid":"monomer-library-input",onChange:a,placeholder:"Search by name..."})]}),(0,n.Y)(Xn,{title:"Hide library",onClick:e,"data-testid":"hide-monomer-library",children:(0,n.Y)(f.In,{name:"arrows-right"})})]}),(0,n.Y)(Mr,{tabs:d,selectedTabIndex:o,onChange:c})]})},is=g.A.div(({theme:e})=>({margin:0,fontSize:e.ketcher.font.size.regular,position:"absolute",top:"12px",cursor:"pointer",visibility:"visible",opacity:1,whiteSpace:"nowrap",display:"flex",alignItems:"center",lineHeight:1,userSelect:"none",backgroundColor:e.ketcher.color.button.primary.active,color:e.ketcher.color.button.text.primary,right:"12px",padding:"10px 8px",borderRadius:"4px","& > span":{display:"flex",justifyContent:"center",alignItems:"center","&.icon":{marginRight:"2px"}}})),as=({onClick:e})=>(0,n.FD)(is,{onClick:e,"data-testid":"show-monomer-library",children:[(0,n.Y)("span",{className:"icon",children:(0,n.Y)(f.In,{name:"arrows-left"})})," ","Show Library"]}),ss=g.A.div`
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: flex-start;
  width: 40px;
  height: 12px;
  margin-top: 10px;

  & span {
    display: inline-block;
    width: 8px;
    height: 8px;
    border: 2px solid ${({theme:e})=>e.ketcher.color.spinner};
    border-radius: 100%;
    box-sizing: border-box;

    &:nth-of-type(1) {
      animation: bounce 1s ease-in-out infinite;
    }

    &:nth-of-type(2) {
      animation: bounce 1s ease-in-out 0.33s infinite;
    }

    &:nth-of-type(3) {
      animation: bounce 1s ease-in-out 0.66s infinite;
    }
  }

  @keyframes bounce {
    0%,
    75%,
    100% {
      -webkit-transform: translateY(0);
      -ms-transform: translateY(0);
      -o-transform: translateY(0);
      transform: translateY(0);
    }

    25% {
      -webkit-transform: translateY(-100%);
      -ms-transform: translateY(-100%);
      -o-transform: translateY(-100%);
      transform: translateY(-100%);
    }
  }
`,ls=()=>(0,n.FD)(ss,{className:"loading-spinner","data-testid":"loading-spinner",children:[(0,n.Y)("span",{}),(0,n.Y)("span",{}),(0,n.Y)("span",{})]}),ds=g.A.div`
  width: 410px;
  min-height: 23em;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
`,cs=g.A.div`
  display: flex;
  flex-direction: row;
  align-items: center;

  svg {
    margin-right: 13px;
  }

  p {
    color: #585858;
    font-size: 16px;
    line-height: 19px;
  }
`,ps=({fileName:e})=>(0,n.FD)(ds,{children:[e&&(0,n.FD)(cs,{children:[(0,n.Y)(f.In,{name:"file-thumbnail"}),(0,n.Y)("p",{children:e})]}),(0,n.Y)(ls,{})]}),us=g.A.p`
  font-size: 10px;
  text-transform: uppercase;
  color: ${({theme:e})=>e.ketcher.color.text.light};
  margin: 0;
  text-align: center;
  line-height: 12px;
`,hs=g.A.p`
  font-size: 10px;
  margin: 0;
  text-align: center;
  color: ${({theme:e})=>e.ketcher.color.text.primary};
  opacity: 50%;
  line-height: 12px;
`,ms={width:"100%",height:"100%",display:"flex",alignItems:"center",flexDirection:"column",justifyContent:"space-between"},gs=(0,g.A)(f.In)`
  filter: ${({disabled:e})=>e?"grayscale(1)":""};
  opacity: ${({disabled:e})=>e?"0.6":"1"};
`,fs={backgroundColor:"#F8FEFFFF"},bs=g.A.div`
  display: flex;
  flex-direction: column;
  align-items: center;

  & > span {
    font-size: ${({theme:e})=>e.ketcher.font.size.small};
    color: ${({theme:e})=>e.ketcher.color.text.primary};
    opacity: 50%;
  }
  & > svg {
    margin-bottom: 8px;
  }
`,xs=g.A.button`
  all: unset;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  flex-direction: column;
  justify-content: space-between;
  cursor: pointer;

  &:disabled {
    cursor: default;
  }
`,ys=({textLabel:e,iconName:t,disabled:o,disabledText:r,...a})=>{const{getRootProps:s,getInputProps:l,isDragActive:d,open:c}=(0,U.VB)({multiple:!1,noClick:!0,disabled:o,onFileDialogOpen:()=>{document.fullscreenElement&&(window.isKetcherFullscreenBeforeFilePicker=!0)},onFileDialogCancel:()=>{const e=window;e.isKetcherFullscreenBeforeFilePicker&&(document.documentElement.requestFullscreen?.().catch(()=>{}),e.isKetcherFullscreenBeforeFilePicker=!1)},...a}),p=(0,i.useMemo)(()=>({...ms,...d?fs:{}}),[d]);return(0,n.FD)(xs,{...s({style:p}),role:void 0,onClick:c,onKeyDown:e=>{"Enter"!==e.key&&" "!==e.key||(e.preventDefault(),o||c())},tabIndex:o?-1:0,disabled:o,type:"button",children:[(0,n.Y)("input",{...l()}),(0,n.Y)(gs,{name:t,disabled:o}),o?(0,n.Y)(hs,{children:r}):(0,n.FD)(n.FK,{children:[(0,n.Y)(bs,{children:e&&(0,n.Y)("span",{children:e})}),(0,n.Y)(us,{children:"Open from file"})]})]})},vs="open-window-paste-icon",ws="open-window-upload-icon",As="image-frame",Cs=g.A.div`
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
  & > * {
    margin-right: 18px;
  }

  & :last-child {
    margin-right: 0;
  }
`,Ms=g.A.div`
  width: 128px;
  height: 134px;
  box-shadow: 0 4px 12px rgba(103, 104, 132, 0.18);
  padding: 16px;
  border-radius: 12px;
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: center;
  box-sizing: border-box;
  position: relative;
  cursor: pointer;

  & > p {
    margin-top: 6px;
    text-align: center;
  }

  svg {
    fill: ${({theme:e})=>e.ketcher.color.button.primary.active};
  }
`,ks=({selectClipboard:e,fileLoadHandler:t,errorHandler:o})=>(0,n.FD)(Cs,{children:[(0,n.FD)(Ms,{"data-testid":"paste-from-clipboard-button",onClick:e,children:[(0,n.Y)(f.In,{name:vs}),(0,n.Y)(us,{children:"Paste from clipboard"})]}),(0,n.Y)(Ms,{"data-testid":"open-from-file-button",children:(0,n.Y)(ys,{onDropAccepted:t,onDropRejected:e=>o(`Unable to accept file(s). ${e}`),buttonLabel:"Open from file",textLabel:"or drag file here",iconName:ws})}),(0,n.Y)(Ms,{"data-testid":"open-from-image-button",children:(0,n.Y)(ys,{accept:"image/*",onDropAccepted:t,onDropRejected:e=>o(`Unable to accept file(s). ${e}`),buttonLabel:"Open from image",textLabel:"or drag file here",iconName:As,disabled:!0,disabledText:"Image Recognition service is not available"})})]}),Is=g.A.textarea`
  min-width: 430px;
  padding: 12px;
  width: 100%;
  height: 100%;
  overflow: auto;
  white-space: pre-wrap;
  resize: none;
  box-sizing: border-box;
  outline: transparent;
  border: none;
  color: ${({theme:e})=>e.ketcher.color.input.text.active};
  font-size: ${({theme:e})=>e.ketcher.font.size.regular};
  background-color: ${({theme:e,readOnly:t})=>t?e.ketcher.color.input.background.disabled:e.ketcher.color.input.background.primary};

  ${({theme:e})=>ha(e)};
`,Ss=({value:e,inputHandler:t,readonly:o=!1,selectOnInit:r=!1,className:a,testId:s})=>{const l=(0,i.useRef)(null);return(0,i.useEffect)(()=>{r&&l.current?.select()},[l,e,r]),(0,n.Y)(Is,{value:e,readOnly:o,onChange:t&&(e=>t(e.target.value)),ref:l,className:a,"data-testid":s})},Ys=(0,g.A)(Ss)`
  width: 100%;
  height: 320px;
`,Ls=e=>{if(e.isAnalyzingFile)return(0,n.Y)(ps,{fileName:e.fileName});switch(e.currentState){case e.states.openOptions:return(0,n.Y)(ks,{...e});case e.states.textEditor:return(0,n.Y)(Ys,{testId:"open-structure-textarea",...e});default:return null}};function Ds(e){return new Promise((t,o)=>{const n=new FileReader;n.onload=()=>{const e=n.result;"string"===typeof e?t(e):o(new Error("Failed to read file as text"))},n.onerror=e=>{o(new Error(`Failed to read file: ${e.type}`))},n.readAsText(e,"UTF-8")})}const Es={openOptions:"openOptions",textEditor:"textEditor"},Fs=g.A.div`
  position: relative;
  padding: ${({currentState:e})=>e===Es.openOptions?"10px 12px":"0"};
`;(0,g.A)(ka)`
  margin-right: auto;
`,(0,g.A)(f.In)`
  fill: #343434;
`;const Ns=(0,g.A)(f.In)`
  user-select: none;
  width: 16px;
  height: 1em;
  display: inline-block;
  flex-shrink: 0;
  transition: fill 200ms cubic-bezier(0.4, 0, 0.2, 1) 0ms;
  font-size: 1.5rem;
  position: absolute;
  right: 7px;
  top: calc(50% - 0.5em);
  pointer-events: none;
  fill: #5b6077;
`,Ps=({className:e})=>(0,n.Y)(Ns,{name:"chevron",className:e}),Bs=(0,g.A)(I.A)`
  width: 100%;
  padding: 0 8px;

  & label {
    font-size: 12px;
    line-height: unset;
  }
`,Ts=(0,g.A)(S.A)`
  height: 24px;
  border: 1px solid #e1e5ea;
  border-radius: 4px;
  font-size: 12px;
  background-color: white;

  ${({open:e})=>e&&a.AH`
      background-color: white;
      border-bottom-left-radius: 0;
      border-bottom-right-radius: 0;
    `}

  & .MuiSelect-select {
    padding: 0 24px 0 8px;
    height: 100%;
    display: flex;
    align-items: center;
  }

  & span {
    ${({theme:e})=>`font-size: ${e.ketcher.font.size.regular}`}
  }

  & .MuiOutlinedInput-notchedOutline {
    border: 0;
  }
`,Rs={backgroundColor:"white",border:"1px solid #5B6077",borderTopWidth:"0",borderRadius:"0px 0px 2px 2px",boxShadow:"0 6px 10px rgba(103, 104, 132, 0.15)"},Os=(0,g.A)(Y.A)`
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  padding: 0 8px;
  height: 28px;
  font-size: 12px;

  &.MuiButtonBase-root:hover {
    border-left: 2px solid #167782;
  }

  & .MuiTypography-root {
    ${({theme:e})=>`font-size: ${e.ketcher.font.size.regular}`}
  }
`,zs=({options:e,currentSelection:t,selectionHandler:o,className:r,label:a,testId:s,customStylesForExpanded:l={},disabled:d=!1})=>{const[c,p]=(0,i.useState)(!1),u=!!document.fullscreenElement?document.querySelector("#root"):void 0;return(0,n.FD)(Bs,{className:r,children:[a&&(0,n.Y)("label",{htmlFor:"fileformat",children:"File format:"}),(0,n.Y)(Ts,{value:t,onChange:e=>{o(e.target.value)},open:c,onOpen:e=>{"keydown"!==e.type&&p(!0)},onClose:()=>{p(!1)},renderValue:t=>{const o=e.filter(e=>e.id===t)[0];return(0,n.Y)("span",{children:o.label})},IconComponent:Ps,disabled:d,fullWidth:!0,"data-testid":s??"dropdown-select",MenuProps:{container:u,PaperProps:{style:{...Rs,...l}},MenuListProps:{style:{padding:"0"}}},children:e.map(e=>(0,n.Y)(Os,{value:e.id,"data-testid":`${e.label}-option`,children:(0,n.Y)(L.A,{primary:e.label})},e.id))})]})},$s=g.A.form({display:"flex",flexDirection:"column",height:"300px"}),js=g.A.div({display:"flex",justifyContent:"space-between",alignItems:"flex-end",marginBottom:"16px"}),_s=(0,g.A)(zs)(({theme:e})=>({width:"230px",flexShrink:0,"& .MuiOutlinedInput-root:hover:not(.Mui-disabled)":{border:`1px solid ${e.ketcher.color.input.border.hover}`},"& .MuiOutlinedInput-root":{border:`1px solid ${e.ketcher.color.input.border.regular}`,backgroundColor:e.ketcher.color.background.primary,color:e.ketcher.color.text.primary,fontFamily:e.ketcher.font.family.inter}})),Hs={border:"none"},qs=g.A.div`
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  justify-content: center;
  align-items: center;
  background: #fff;
`,Vs=(0,g.A)("div")(({theme:e})=>({height:"100%",position:"relative",border:`1px solid ${e.ketcher.color.input.border.regular}`,"& svg":{width:"100%",height:"100%","& .drawn-structures":{"& .monomer":{lineHeight:"initial !important"}}}})),Ws=(0,g.A)("div")(({theme:e})=>({display:"flex",flexGrow:1,position:"relative","& button":{opacity:0,position:"absolute",right:"12px",top:"12px",borderRadius:"4px",padding:"2px",width:"28px",height:"28px","&:not(:active)":{backgroundColor:e.ketcher.color.background.primary,color:e.ketcher.color.text.primary}},"&:hover button":{opacity:1},"&:focus-within button":{opacity:0},"&:focus-within button:hover":{opacity:1}})),Us=(0,g.A)(wa)(({modalWidth:e})=>`\n    .MuiPaper-root {\n      width: ${e};\n      max-width: ${e};\n    }`),Ks=g.A.div({width:"100%",display:"flex",flexDirection:"row",justifyContent:"space-between",alignItems:"center"}),Gs=g.A.div({display:"flex",height:"24px",fontSize:"12px"}),Qs=(0,g.A)(_s)({padding:0,fontSize:"12px","& .MuiSelect-select":{display:"flex",alignItems:"center",padding:"0 20px 0 8px",paddingRight:"20px !important",height:"100%"},"& span":{fontSize:"12px"}}),Zs=(0,g.A)(Qs)(()=>({width:"140px"})),Xs=(0,g.A)(Qs)({width:"76px",marginLeft:"8px"}),Js=(0,g.A)(Qs)({width:"105px",marginLeft:"8px"}),el=(0,g.A)("div")({display:"flex",gap:"10px"}),tl=(0,g.A)(ka)({width:"min-content"}),ol="ket",nl="seq",rl="rna",il="peptide",al="fasta",sl="one-letter",ll="three-letter",dl=[{id:"ket",label:"Ket Format"},{id:"mol",label:"MDL Molfile V3000"},{id:"seq",label:"Sequence"},{id:"fasta",label:"FASTA"},{id:"idt",label:"IDT"},{id:"axo-labs",label:"AxoLabs"},{id:"helm",label:"HELM"},{id:"biln",label:"BILN"}],cl=[{id:rl,label:"RNA"},{id:"dna",label:"DNA"},{id:il,label:"Peptide"}],pl=[{id:sl,label:"1-letter code"},{id:ll,label:"3-letter code"}],ul=c.vsV,hl=({ketSerializer:e,editor:t,struct:o})=>{const n=!t.drawingEntitiesManager.hasDrawingEntities,r=e.deserializeToDrawingEntities(o);if(!r)throw new Error("Error during parsing file");if(!r.drawingEntitiesManager.hasDrawingEntities)return!1;const i="sequence-layout-mode"===t.mode.modeName,a="snake-layout-mode"===t.mode.modeName,s="flex-layout-mode"===t.mode.modeName;i&&!n?(e=>{const t=c.HQ1.getNextChainPosition(),o=e.allEntities[0]?.[1].position;if(!o)return;const n=c.ZY9.diff(t,new c.ZY9(o));e.allEntities.forEach(([,t])=>{e.moveDrawingEntityModelChange(t,n)})})(r.drawingEntitiesManager):r.drawingEntitiesManager.centerMacroStructure();const{command:l}=r.drawingEntitiesManager.mergeInto(t.drawingEntitiesManager),d=c.kPZ.getInstance(t);return s&&l.merge(t.drawingEntitiesManager.recalculateAntisenseChains()),t.drawingEntitiesManager.detectBondsOverlappedByMonomers(),t.renderersContainer.update(l),d.update(l),i&&(l.setUndoOperationReverse(),t.events.selectMode.dispatch({mode:c.hJO.sequence,mergeWithLatestHistoryCommand:!0})),a&&(l.setUndoOperationReverse(),t.events.selectMode.dispatch({mode:c.hJO.snake,mergeWithLatestHistoryCommand:!0})),n&&t.zoomToStructuresIfNeeded(),t.calculateAndStoreNextAutochainPosition(r.drawingEntitiesManager),!0},ml=async({struct:e,formatSelection:t,additionalSelection:o,peptideLettersFormatSelection:n,onCloseCallback:r,setIsLoading:i,dispatch:a})=>{const s=t===ol,l=t===nl,d=t===al,p=new c.BBJ,u=(0,c.uui)();let h,m=e;const g=e=>{a(Fo({errorMessage:"Convert error! "+e,errorTitle:l||d?"Unsupported symbols":""}))};if(s){try{hl({struct:e,ketSerializer:p,editor:u})||a(Do("No structure")),r()}catch(x){g("Error during file parsing.")}return}d||l&&n!==ll?(h=ul[t][o],m=m.toUpperCase()):h=l&&n===ll?ul.seq.peptide3Letter:ul[t];const b=f.e3.getIndigo();try{i(!0);const e=await b.convert({struct:m,output_format:c.Raq.KET,input_format:h});hl({struct:e.struct,ketSerializer:p,editor:u})||a(Do("No structure")),r()}catch(y){g((0,c.cQb)(y).message),c.$yr.error(y)}finally{i(!1)}},gl=!1,fl=e=>{},bl=g.A.label(({theme:e})=>({display:"flex",flexDirection:"column",marginRight:"8px",color:e.ketcher.color.text.primary,fontSize:"12px"})),xl=g.A.input(({theme:e})=>({height:"24px",padding:"3px 7px",border:`1px solid ${e.ketcher.color.input.border.regular}`,fontSize:"14px",borderRadius:"4px",backgroundColor:e.ketcher.color.input.background.primary,color:e.ketcher.color.text.primary,outline:"transparent",width:"164px",letterSpacing:"normal","&:active, &:focus":{color:e.ketcher.color.input.text.active},"&:hover":{border:`1px solid ${e.ketcher.color.input.border.hover}`}})),yl=({value:e,id:t,onChange:o,label:r,type:i,className:a,inputClassName:s,...l})=>(0,n.FD)(bl,{htmlFor:t,className:a,children:[r&&(0,n.Y)("span",{children:r}),(0,n.Y)(xl,{type:i||"text",id:t,value:e,className:s,onChange:e=>{o(e.target.value)},...l})]});var vl;!function(e){e.Ket="chemical/x-ket",e.Mol="chemical/x-mdl-molfile",e.HELM="chemincal/x-helm",e.Fasta="chemical/x-fasta",e.Sequence="chemical/x-sequence",e.Idt="chemical/x-idt",e.AxoLabs="chemical/x-axo-labs",e.Svg="image/svg+xml",e.BILN="chemical/x-biln"}(vl||(vl={}));class wl{name;mime;extensions;supportsCoords;options;constructor(e,t,o,n,r){this.name=e,this.mime=t,this.extensions=o,this.supportsCoords=n??!1,this.options=r??{}}}const Al={ket:new wl("Ket file",vl.Ket,[".ket"],!0,{}),mol:new wl("MDL Molfile V3000",vl.Mol,[".mol"],!0,{"molfile-saving-mode":"3000"}),fasta:new wl("FASTA",vl.Fasta,[".fasta"],!1,{}),sequence:new wl("SEQUENCE",vl.Sequence,[".seq"],!1,{}),"sequence-3-letter":new wl("SEQUENCE (3-letter code)",vl.Sequence,[".seq"],!1,{}),idt:new wl("IDT",vl.Idt,[".idt"],!1,{}),"axo-labs":new wl("AxoLabs",vl.AxoLabs,[".axolabs"],!1,{}),helm:new wl("HELM",vl.HELM,[".helm"]),biln:new wl("BILN",vl.BILN,[".biln"]),svg:new wl("SVG Document",vl.Svg,[".svg"])},Cl=e=>Al[e],Ml=[{id:"ket",label:"Ket Format"},{id:"mol",label:"MDL Molfile V3000"},{id:"sequence",label:"Sequence (1-letter code)"},{id:"sequence-3-letter",label:"Sequence (3-letter code)"},{id:"fasta",label:"FASTA"},{id:"idt",label:"IDT"},{id:"axo-labs",label:"AxoLabs"},{id:"svg",label:"SVG Document"},{id:"helm",label:"HELM"},{id:"biln",label:"BILN"}],kl={mol:c.Raq.Mol,fasta:c.Raq.FASTA,sequence:c.Raq.SEQUENCE,"sequence-3-letter":c.Raq.PeptideSequenceThreeLetter,idt:c.Raq.IDT,"axo-labs":c.Raq.AXOLABS,helm:c.Raq.HELM,biln:c.Raq.BILN},Il=(0,g.A)(wa)({"& div.MuiPaper-root":{background:"white",minHeight:"400px",minWidth:"430px"},"& .MuiDialogContent-root":{overflow:"hidden",height:"100%"}}),Sl=(0,g.A)(ka)(()=>({width:"72px"})),Yl=g.A.div`
  padding: 12px;
`,Ll=g.A.div`
  padding: 12px;
`,Dl=g.A.div({display:"flex",flexDirection:"column",rowGap:"2px",alignItems:"center",marginBottom:"5px"}),El=g.A.span(e=>({margin:0,padding:0,textAlign:"center",display:"block",font:e.theme.ketcher.font.family.inter,fontSize:e.theme.ketcher.font.size.small,fontWeight:e.theme.ketcher.font.weight.regular,color:e.disabled?"rgba(180, 185, 214, 1)":e.theme.ketcher.color.text.light})),Fl=g.A.div(()=>({height:"100%"})),Nl=(0,g.A)(f.c9,{shouldForwardProp:e=>"isExpanded"!==e})(({theme:e,isExpanded:t})=>({display:"flex",border:`1.5px solid ${e.ketcher.outline.color}`,borderRadius:"6px",padding:5,maxHeight:"100%",minHeight:"150px",height:t?"auto":"150px",width:t?"auto":"150px",alignSelf:"stretch","& svg":{maxWidth:"fit-content",margin:"auto"}})),Pl=g.A.div({display:"flex",flexWrap:"wrap",justifyContent:"center",alignSelf:"flex-start",width:"100%",gap:"7px"}),Bl=(0,g.A)("div",{shouldForwardProp:e=>"isExpanded"!==e})(({isExpanded:e})=>({minHeight:"150px",height:e?"auto":"150px",width:e?"auto":"150px",alignSelf:"stretch"})),Tl=g.A.div`
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 8px;
  padding: 8px;
  color: #7c7c7f;
  border: 1px solid #cad3dd;
  width: 100%;
  height: 100%;
`,Rl=({testId:e})=>(0,n.FD)(Tl,{"data-testid":e,children:[(0,n.Y)(f.In,{name:"questionMark"}),"Unknown structure"]}),Ol=150,zl=g.A.div(({theme:e,expanded:t})=>({display:"flex",border:`1.5px solid ${e.ketcher.outline.color}`,borderRadius:"6px",padding:5,maxHeight:"100%",minHeight:"150px",height:t?"auto":"150px",width:t?"auto":"150px",alignSelf:"stretch","& svg":{maxWidth:"fit-content",margin:"auto"}})),$l=({monomer:e,expanded:t,selectedAttachmentPoint:o,connectedAttachmentPoints:r,usage:a,testId:s})=>{const l=(0,i.useRef)(null),d=(0,i.useRef)(null),p=(0,i.useCallback)(()=>{const e=d.current,t=l.current;if(!e||!t)return;const{width:o,height:n}=t.getBoundingClientRect();if(0===o||0===n)return;const r=Math.min(o,n)/Ol;(0,$.Ltv)(e).style("transform",`scale(${r})`),(0,$.Ltv)(e).style("transform-origin","center")},[]);return(0,i.useLayoutEffect)(()=>{const t=d.current;if(t){const n=(0,$.Ltv)(t);if(n.selectAll("*").remove(),e instanceof c.cN8){const t=75,i=75,s=new c.ZY9(t,i),l=c.E$k.canvasToModel(s),d=new c.cN8(e.variantMonomerItem,l);new c.TYn(d).showExternal({canvas:n,usage:a,selectedAttachmentPoint:o,connectedAttachmentPoints:r})}}p()},[o,r,t,e,a,p]),(0,i.useLayoutEffect)(()=>{const e=l.current;if(!e)return;const t=new ResizeObserver(p);return t.observe(e),()=>t.disconnect()},[p]),(0,n.Y)(zl,{ref:l,expanded:t,"data-testid":s,children:(0,n.Y)("svg",{ref:d,width:Ol,height:Ol})})},jl=({monomer:e,attachmentPoints:t,connectedAttachmentPoints:o,selectedAttachmentPoint:r,usage:i,needCache:a,update:s,expanded:l,testId:d})=>{const p=e instanceof c.cN8,u=e.monomerItem.props.unresolved;let h;return h=p?(0,n.Y)($l,{monomer:e,expanded:l,selectedAttachmentPoint:r,connectedAttachmentPoints:o,usage:i,testId:d}):u?(0,n.Y)(Bl,{isExpanded:l,children:(0,n.Y)(Rl,{testId:d})}):(0,n.Y)(Nl,{struct:e.monomerItem.struct,options:{connectedMonomerAttachmentPoints:o,currentlySelectedMonomerAttachmentPoint:r??void 0,usageInMacromolecule:i,labelInMonomerConnectionsModal:!0,needCache:a??!1},update:s,isExpanded:l,testId:d}),(0,n.FD)(n.FK,{children:[h,(0,n.Y)(Pl,{children:(0,n.Y)(n.FK,{children:t})})]})},_l=g.A.div(({theme:e,isExpanded:t})=>({margin:0,padding:0,textAlign:"center",alignSelf:"flex-end",display:"block",font:e.ketcher.font.family.inter,fontSize:e.ketcher.font.size.regular,fontWeight:e.ketcher.font.weight.regular,flexBasis:"200px",lineHeight:"14px",maxWidth:t?void 0:"150px"})),Hl=g.A.div(({theme:e})=>({width:10,height:2,display:"block",color:e.ketcher.color.button.secondary.hover,backgroundColor:e.ketcher.color.button.secondary.hover,borderRadius:20,margin:"auto"})),ql=g.A.div(()=>({display:"grid",gridAutoFlow:"column",gridTemplateRows:"auto auto auto",gridTemplateColumns:"1fr 10px 1fr",justifyContent:"center",alignItems:"flex-start",padding:"12px",paddingBottom:0,height:"100%",columnGap:"12px",rowGap:"8px"})),Vl=e=>e instanceof c.cN8?(e=>{const{monomerClass:t,variantMonomerItem:o}=e,n=o.label,r=o.options??[];if(t===c.IPc.Base){const e=r.some(e=>e.templateId.toLowerCase().includes("thymine")),t=r.some(e=>e.templateId.toLowerCase().includes("uracil"));return e?"N"===n?"Any DNA base":"Ambiguous DNA Base":t?"N"===n?"Any RNA Base":"Ambiguous RNA Base":"Ambiguous Base"}return t===c.IPc.AminoAcid?"X"===n?"Any Amino acid":"Ambiguous Amino acid":`Ambiguous ${t}`})(e):e.monomerItem.props.Name,Wl=({firstMonomer:e,secondMonomer:t,expanded:o,firstMonomerOverview:r,secondMonomerOverview:i})=>{const a=Vl(e),s=Vl(t);return(0,n.Y)(ql,{children:(0,n.FD)(n.FK,{children:[(0,n.Y)(_l,{isExpanded:o,children:a}),r,(0,n.Y)("span",{}),(0,n.Y)(Hl,{}),(0,n.Y)("span",{}),(0,n.Y)(_l,{isExpanded:o,children:s}),i]})})},Ul=(0,g.A)(wa)({"& .MuiPaper-root":{background:"#fff !important"},"& .MuiDialogContent-root":{overflow:"hidden"}}),Kl=(0,g.A)(ka)(()=>({width:"97px !important"})),Gl=(0,g.A)(ka)(e=>({width:"97px !important",color:e.disabled?"rgba(51, 51, 51, 0.6)":"",background:e.disabled?"rgba(225, 229, 234, 1) !important":"",opacity:"1 !important",minHeight:"0"})),Ql=(0,g.A)(ka)(e=>({borderRadius:5,minWidth:"45px !important",padding:"4px",border:`1px solid ${e.theme.ketcher.color.border.secondary}`,color:e.disabled?"rgba(51, 51, 51, 0.6) !important":"",background:e.disabled?"rgba(225, 229, 234)":"",borderColor:e.disabled?"rgba(225, 229, 234) !important":""}));function Zl({monomer:e,selectedAttachmentPoint:t,onSelectAttachmentPoint:o,expanded:r=!1,position:a}){const[s,l]=(0,i.useState)(()=>Sr(e.attachmentPointsToBonds)),d=t=>{const o=e.monomerCaps,n=e instanceof c.cN8;if(!o)return n?null:"H";return o[t]??null};return(0,n.Y)(jl,{monomer:e,connectedAttachmentPoints:s,selectedAttachmentPoint:t,usage:c.HT5.MonomerConnectionsModal,update:r,expanded:r,attachmentPoints:e.listOfAttachmentPoints.map(r=>{const i=Boolean(s.includes(r)&&r!==t);return(0,n.FD)(Dl,{children:[(0,n.Y)(Ql,{label:r,styleType:r===t?"primary":"secondary",clickHandler:()=>(n=>{const r=t?e.attachmentPointsToBonds[t]:null;t&&r&&e.removeBond(r),l(Sr(e.attachmentPointsToBonds)),o(n)})(r),disabled:i,"data-testid":`${a}-${r}`,"data-isactive":r===t}),(0,n.Y)(El,{"data-testid":"leaving-group-value",disabled:i,children:d(r)})]},r)}),testId:`${a}-monomer-preview`})}function Xl(e){if(e.chosenFirstAttachmentPointForBond)return e.chosenFirstAttachmentPointForBond;if(e.chosenSecondAttachmentPointForBond)return e.chosenSecondAttachmentPointForBond;const t=Object.entries(e.attachmentPointsToBonds).filter(([e,t])=>null==t);if(1===t.length){const[e]=t[0];return e}return null}const Jl=g.A.p`
  padding: 12px;
  font-size: 12px;
  color: #000;
`,ed=[{id:"general",title:"General",fields:["resetToSelect","rotationStep","showValenceWarnings","atomColoring","font","fontsz","fontszUnit","fontszsub","fontszsubUnit","reactionComponentMarginSize","reactionComponentMarginSizeUnit","imageResolution"]},{id:"stereochemistry",title:"Stereochemistry",fields:["showStereoFlags","stereoLabelStyle","colorOfAbsoluteCenters","colorOfAndCenters","colorOfOrCenters","colorStereogenicCenters","autoFadeOfStereoLabels","absFlagLabel","andFlagLabel","orFlagLabel","mixedFlagLabel","ignoreChiralFlag"]},{id:"atoms",title:"Atoms",fields:["carbonExplicitly","showCharge","showValence","showHydrogenLabels"]},{id:"bonds",title:"Bonds",fields:["aromaticCircle","bondSpacing","bondThickness","bondThicknessUnit","stereoBondWidth","stereoBondWidthUnit","hashSpacing","hashSpacingUnit"]},{id:"server",title:"Server",fields:["smart-layout","ignore-stereochemistry-errors","mass-skip-error-on-pseudoatoms","gross-formula-add-rsites","gross-formula-add-isotopes"]},{id:"viewer3d",title:"3D Viewer",fields:["miewMode","miewTheme","miewAtomLabel"]},{id:"debug",title:"Options for Debugging",fields:["showAtomIds","showBondIds","showHalfBondIds","showLoopIds"]}],td={resetToSelect:{label:"Reset to Select Tool",type:"select",options:[{value:!0,label:"Enabled"},{value:!1,label:"Disabled"},{value:"paste",label:"After Paste"}]},rotationStep:{label:"Rotation Step (degrees)",type:"number",min:1,max:90,step:1},showValenceWarnings:{label:"Show Valence Warnings",type:"checkbox"},atomColoring:{label:"Atom Coloring",type:"checkbox"},font:{label:"Font",type:"select",options:[{value:"30px Arial",label:"Arial"},{value:"30px Arial Black",label:"Arial Black"},{value:"30px Comic Sans MS",label:"Comic Sans MS"},{value:"30px Courier New",label:"Courier New"},{value:"30px Georgia",label:"Georgia"},{value:"30px Impact",label:"Impact"},{value:"30px Charcoal",label:"Charcoal"},{value:"30px Lucida Console",label:"Lucida Console"},{value:"30px Monaco",label:"Monaco"},{value:"30px Palatino Linotype",label:"Palatino Linotype"},{value:"30px Book Antiqua",label:"Book Antiqua"},{value:"30px Palatino",label:"Palatino"},{value:"30px Tahoma",label:"Tahoma"},{value:"30px Geneva",label:"Geneva"},{value:"30px Times New Roman",label:"Times New Roman"},{value:"30px Times",label:"Times"},{value:"30px Verdana",label:"Verdana"},{value:"30px Symbol",label:"Symbol"},{value:"30px MS Serif",label:"MS Serif"},{value:"30px MS Sans Serif",label:"MS Sans Serif"},{value:"30px New York",label:"New York"},{value:"30px Droid Sans",label:"Droid Sans"},{value:"30px Droid Serif",label:"Droid Serif"},{value:"30px Droid Sans Mono",label:"Droid Sans Mono"},{value:"30px Roboto",label:"Roboto"}]},fontsz:{label:"Font Size",type:"number",min:1,max:96,step:1},fontszUnit:{label:"Font Size Unit",type:"select",options:[{value:"px",label:"px"},{value:"pt",label:"pt"},{value:"cm",label:"cm"},{value:"inch",label:"inch"}]},fontszsub:{label:"Sub/Superscript Font Size",type:"number",min:1,max:96,step:1},fontszsubUnit:{label:"Sub/Superscript Font Size Unit",type:"select",options:[{value:"px",label:"px"},{value:"pt",label:"pt"},{value:"cm",label:"cm"},{value:"inch",label:"inch"}]},reactionComponentMarginSize:{label:"Reaction Component Margin Size",type:"number",min:.1,max:1e3,step:.1},reactionComponentMarginSizeUnit:{label:"Reaction Component Margin Size Unit",type:"select",options:[{value:"px",label:"px"},{value:"pt",label:"pt"},{value:"cm",label:"cm"},{value:"inch",label:"inch"}]},imageResolution:{label:"Image Resolution",type:"select",options:[{value:"72",label:"low (72 DPI)"},{value:"600",label:"high (600 DPI)"}]},showStereoFlags:{label:"Show Stereo Flags",type:"checkbox"},stereoLabelStyle:{label:"Label Display at Stereogenic Centers",type:"select",options:[{value:"Iupac",label:"IUPAC style"},{value:"Classic",label:"Classic"},{value:"On",label:"On"},{value:"Off",label:"Off"}]},colorOfAbsoluteCenters:{label:"Color of Absolute Centers",type:"color"},colorOfAndCenters:{label:"Color of AND Centers",type:"color"},colorOfOrCenters:{label:"Color of OR Centers",type:"color"},colorStereogenicCenters:{label:"Color Stereogenic Centers",type:"select",options:[{value:"LabelsOnly",label:"Labels Only"},{value:"BondsOnly",label:"Bonds Only"},{value:"LabelsAndBonds",label:"Labels and Bonds"},{value:"Off",label:"Off"}]},autoFadeOfStereoLabels:{label:"Auto Fade of Stereo Labels",type:"checkbox"},absFlagLabel:{label:"Absolute Flag Label",type:"text"},andFlagLabel:{label:"AND Flag Label",type:"text"},orFlagLabel:{label:"OR Flag Label",type:"text"},mixedFlagLabel:{label:"Mixed Flag Label",type:"text"},ignoreChiralFlag:{label:"Ignore Chiral Flag",type:"checkbox"},carbonExplicitly:{label:"Show Carbon Explicitly",type:"checkbox"},showCharge:{label:"Show Charge",type:"checkbox"},showValence:{label:"Show Valence",type:"checkbox"},showHydrogenLabels:{label:"Show Hydrogen Labels",type:"select",options:[{value:"off",label:"Off"},{value:"Hetero",label:"Heteroatoms"},{value:"Terminal",label:"Terminal"},{value:"Terminal and Hetero",label:"Terminal and Hetero"},{value:"On",label:"On"}]},aromaticCircle:{label:"Aromatic Circle",type:"checkbox"},bondSpacing:{label:"Bond Spacing",type:"number",min:.1,max:10,step:.1},bondThickness:{label:"Bond Thickness",type:"number",min:.1,max:96,step:.1},bondThicknessUnit:{label:"Bond Thickness Unit",type:"select",options:[{value:"px",label:"px"},{value:"pt",label:"pt"},{value:"cm",label:"cm"},{value:"inch",label:"inch"}]},stereoBondWidth:{label:"Stereo Bond Width",type:"number",min:.1,max:96,step:.1},stereoBondWidthUnit:{label:"Stereo Bond Width Unit",type:"select",options:[{value:"px",label:"px"},{value:"pt",label:"pt"},{value:"cm",label:"cm"},{value:"inch",label:"inch"}]},hashSpacing:{label:"Hash Spacing",type:"number",min:.1,max:1e3,step:.1},hashSpacingUnit:{label:"Hash Spacing Unit",type:"select",options:[{value:"px",label:"px"},{value:"pt",label:"pt"},{value:"cm",label:"cm"},{value:"inch",label:"inch"}]},"smart-layout":{label:"Smart Layout",type:"checkbox"},"ignore-stereochemistry-errors":{label:"Ignore Stereochemistry Errors",type:"checkbox"},"mass-skip-error-on-pseudoatoms":{label:"Skip Mass Error on Pseudoatoms",type:"checkbox"},"gross-formula-add-rsites":{label:"Add R-sites to Gross Formula",type:"checkbox"},"gross-formula-add-isotopes":{label:"Add Isotopes to Gross Formula",type:"checkbox"},miewMode:{label:"Miew Mode",type:"select",options:[{value:"LN",label:"Lines"},{value:"BS",label:"Ball and Stick"},{value:"LC",label:"Licorice"}]},miewTheme:{label:"Miew Theme",type:"select",options:[{value:"light",label:"Light"},{value:"dark",label:"Dark"}]},miewAtomLabel:{label:"Miew Atom Label",type:"select",options:[{value:"no",label:"None"},{value:"bright",label:"Bright"},{value:"blackAndWhite",label:"Black and White"},{value:"black",label:"Black"}]},showAtomIds:{label:"Show Atom IDs",type:"checkbox"},showBondIds:{label:"Show Bond IDs",type:"checkbox"},showHalfBondIds:{label:"Show Half-Bond IDs",type:"checkbox"},showLoopIds:{label:"Show Loop IDs",type:"checkbox"}},od=g.A.div`
  padding: 16px;
  max-height: 600px;
  overflow-y: auto;
`,nd=g.A.div`
  display: flex;
  gap: 8px;
  align-items: center;
`,rd=g.A.span`
  padding-right: 4px;
`,id=g.A.button`
  background-color: transparent;
  border: none;
  color: #167782;
  padding: 0;
  font-size: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;

  &:hover {
    color: #005662;
  }

  &:disabled {
    color: #cad3dd;
    cursor: not-allowed;
  }

  input[type='file'] {
    display: none;
  }
`,ad=g.A.div`
  display: flex;
  gap: 8px;
  flex: 1;
`,sd=g.A.div`
  display: flex;
  gap: 8px;
  margin-left: auto;
`,ld=g.A.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 8px 0;
`,dd=g.A.div`
  display: flex;
  align-items: center;
  justify-content: flex-start;
  font-size: 12px;
  line-height: 14px;
  margin-bottom: 12px;
  color: #333333;
  height: 24px;

  label {
    display: flex;
    justify-content: flex-start;
    align-items: center;
    width: 100%;
  }

  label > span:first-of-type {
    width: 168px;
    margin-right: 8px;
  }

  label > input,
  label > select,
  label > div {
    width: 120px;
    margin-right: auto;
  }
`,cd=({name:e,label:t,type:o,value:r,onChange:i,options:a,min:s,max:l,step:d})=>{const c="string"===typeof r?r:void 0,p="number"===typeof r?r:void 0;let u="";"boolean"===typeof r?u=String(r):void 0!==r&&(u=r);return(0,n.Y)(dd,{children:(()=>{switch(o){case"checkbox":return(0,n.FD)("label",{children:[(0,n.Y)("span",{children:t}),(0,n.Y)(D.A,{checked:Boolean(r),onChange:e=>i(e.target.checked),size:"small","data-testid":`setting-${e}`})]});case"number":return(0,n.FD)("label",{children:[(0,n.Y)("span",{children:t}),(0,n.Y)("input",{type:"number",value:p??"",onChange:e=>i(Number(e.target.value)),min:s,max:l,step:d??1,"data-testid":`setting-${e}`,style:{fontSize:"12px",padding:"4px 8px",height:"24px",border:"1px solid #cad3dd",borderRadius:"2px"}})]});case"select":return(0,n.FD)("label",{children:[(0,n.Y)("span",{children:t}),(0,n.Y)(I.A,{size:"small",sx:{border:"none"},children:(0,n.Y)(S.A,{value:u,onChange:e=>{const t=a?.find(t=>String(t.value)===e.target.value);i(t?.value??e.target.value)},displayEmpty:!0,"data-testid":`setting-${e}`,sx:{fontSize:"12px",height:"24px",border:"none","& .MuiOutlinedInput-notchedOutline":{border:"1px solid #cad3dd"},"&:hover .MuiOutlinedInput-notchedOutline":{borderColor:"#167782"},"&.Mui-focused .MuiOutlinedInput-notchedOutline":{borderColor:"#167782"}},children:a?.map(e=>(0,n.Y)(Y.A,{value:String(e.value),sx:{fontSize:"12px"},children:e.label},String(e.value)))})})]});case"color":return(0,n.FD)("label",{children:[(0,n.Y)("span",{children:t}),(0,n.FD)("div",{style:{display:"flex",gap:"4px",alignItems:"center"},children:[(0,n.Y)("input",{type:"color",value:c??"#000000",onChange:e=>i(e.target.value),"data-testid":`setting-${e}`,style:{width:"30px",height:"24px",border:"1px solid #cad3dd",borderRadius:"2px",cursor:"pointer",padding:"2px"}}),(0,n.Y)("input",{type:"text",value:c??"#000000",onChange:e=>i(e.target.value),style:{width:"85px",fontSize:"12px",padding:"4px 8px",height:"24px",border:"1px solid #cad3dd",borderRadius:"2px"}})]})]});default:return(0,n.FD)("label",{children:[(0,n.Y)("span",{children:t}),(0,n.Y)("input",{type:"text",value:c??"",onChange:e=>i(e.target.value),"data-testid":`setting-${e}`,style:{fontSize:"12px",padding:"4px 8px",height:"24px",border:"1px solid #cad3dd",borderRadius:"2px"}})]})}})()})},pd=({fields:e,settings:t,onChange:o})=>(0,n.Y)(ld,{children:e.map(e=>{const r=td[e];return r?(0,n.Y)(cd,{name:String(e),label:r.label,type:r.type,value:t[e],options:r.options,min:r.min,max:r.max,step:r.step,onChange:t=>o({[e]:t})},String(e)):(c.$yr.warn(`Field definition not found: ${String(e)}`),null)})}),ud=g.A.div`
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  line-height: 14px;
  color: #333333;
  font-weight: 400;
`,hd=g.A.div`
  display: flex;
  align-items: center;
  gap: 6px;

  svg {
    width: 15px;
    height: 15px;
  }

  span {
    height: 12px;
  }
`;g.A.span`
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background-color: #167782;
  margin-left: auto;
  margin-right: 8px;
`;const md=({settings:e,onChange:t,expandedGroups:o,onGroupToggle:r})=>(0,n.Y)("div",{children:ed.map(i=>(0,n.FD)(E.A,{expanded:o.includes(i.id),onChange:()=>r(i.id),"data-testid":`${i.title}-accordion`,children:[(0,n.Y)(F.A,{"aria-controls":`${i.id}-content`,id:`${i.id}-header`,children:(0,n.FD)(ud,{children:[(0,n.FD)(hd,{children:[(0,n.Y)(f.In,{name:"elements-group"}),(0,n.Y)("span",{children:i.title})]}),!1]})}),(0,n.Y)(N.A,{children:(0,n.Y)(pd,{fields:i.fields,settings:e,onChange:t})})]},i.id))}),gd={open:({isModalOpen:e,onClose:t})=>{const o=Lr(),[r,a]=(0,i.useState)(""),[s,l]=(0,i.useState)(""),[d,p]=(0,i.useState)(!1),[u,h]=(0,i.useState)(),[m,g]=(0,i.useState)(Es.openOptions),[f,b]=(0,i.useState)(ol),[x,y]=(0,i.useState)(rl),[v,w]=(0,i.useState)(sl);(0,i.useEffect)(()=>{new Promise((e,t)=>{globalThis.FileReader?e(Ds):t(new Error("Your browser does not support opening files locally"))}).then(e=>{h({chosenOpener:e})})},[]);const A=(0,i.useCallback)(()=>{g(Es.openOptions),a(""),b(ol),y(rl),t()},[t]),C=()=>{ml({struct:r,formatSelection:f,additionalSelection:x,peptideLettersFormatSelection:v,onCloseCallback:A,setIsLoading:p,dispatch:o})},M=()=>{const e=(0,c.uui)(),t=c.kPZ.getInstance(e),n=e.drawingEntitiesManager.deleteAllEntities();t.update(n),e.renderersContainer.update(n),e.zoomToStructuresIfNeeded(),ml({struct:r,formatSelection:f,additionalSelection:x,peptideLettersFormatSelection:v,onCloseCallback:A,setIsLoading:p,dispatch:o})};return(0,n.FD)(Us,{isOpen:e,title:"Open Structure",onClose:A,modalWidth:m===Es.textEditor?"620px":"",testId:"openStructureModal",children:[(0,n.Y)(wa.Content,{children:(0,n.FD)(Fs,{currentState:m,children:[(0,n.Y)(Ls,{isAnalyzingFile:gl,fileName:s,currentState:m,states:Es,selectClipboard:()=>g(Es.textEditor),fileLoadHandler:e=>{const t=window;t.isKetcherFullscreenBeforeFilePicker&&(document.documentElement.requestFullscreen?.().catch(()=>{}),t.isKetcherFullscreenBeforeFilePicker=!1);const o=e[0].name,n=o?.split("."),r=n[n.length-1];if(l(o),r){const e=dl.find(e=>e.id===r),t=e?.id?e.id:nl;b(t)}u?.chosenOpener(e[0]).then(e=>{a(e),g(Es.textEditor)},()=>{})},errorHandler:fl,value:r,inputHandler:a}),d&&(0,n.Y)(qs,{children:(0,n.Y)(ls,{})})]})}),m===Es.textEditor?(0,n.Y)(wa.Footer,{withborder:"true",children:(0,n.FD)(Ks,{children:[(0,n.FD)(Gs,{children:[(0,n.Y)(Zs,{options:dl,currentSelection:f,selectionHandler:b,customStylesForExpanded:Hs},f),f===nl||f===al?(0,n.Y)(Xs,{options:cl,currentSelection:x,selectionHandler:y,customStylesForExpanded:Hs,testId:"dropdown-select-type"},x):null,f===nl&&x===il?(0,n.Y)(Js,{options:pl,currentSelection:v,selectionHandler:w,testId:"dropdown-select-peptide-letters-format"}):null]}),(0,n.FD)(el,{children:[(0,n.Y)(tl,{disabled:!r.trim(),clickHandler:M,label:"Open as New",styleType:"secondary","data-testid":"open-as-new-button"},"openButton"),(0,n.Y)(tl,{disabled:!r.trim(),clickHandler:C,label:"Add to Canvas",title:"Structure will be loaded as fragment and added to Clipboard","data-testid":"add-to-canvas-button"},"copyButton")]})]})}):(0,n.Y)(n.FK,{})]})},save:({onClose:e,isModalOpen:t})=>{const o=Lr(),r=f.e3.getIndigo(),a=(0,c.uui)(),[s,l]=(0,i.useState)("ket"),[d,p]=(0,i.useState)("ketcher"),[u,h]=(0,i.useState)(()=>(new c.BBJ).serialize(a.drawingEntitiesManager.micromoleculesHiddenEntities.clone(),a.drawingEntitiesManager)),[m,g]=(0,i.useState)(!1),[b,x]=(0,i.useState)(),y=()=>{let t;if("svg"===s){const o=(0,c.xRJ)(a.canvas,"file");if(!o)return void e();t=o}else t=u;const o=new Blob([t],{type:Cl(s).mime}),n=Cl(s);(0,K.saveAs)(o,`${d}${n.extensions[0]}`),e()};return(0,n.FD)(Il,{title:"save structure",isOpen:t,onClose:e,testId:"save-structure-dialog",children:[(0,n.Y)(wa.Content,{children:(0,n.FD)($s,{onSubmit:y,id:"save",children:[(0,n.FD)(js,{style:{padding:"12px 12px 10px"},children:[(0,n.Y)("div",{children:(0,n.Y)(yl,{value:d,id:"filename",onChange:e=>{p(e)},label:"File name:","data-testid":"filename-input"})}),(0,n.Y)(_s,{label:"File format:",options:Ml,currentSelection:s,selectionHandler:e=>(async e=>{l(e);const t=(new c.BBJ).serialize(a.drawingEntitiesManager.micromoleculesHiddenEntities.clone(),a.drawingEntitiesManager);if(x(void 0),"ket"!==e){if("svg"===e){const e=(0,c.xRJ)(a.canvas,"preview");return void x(e)}"helm"===e&&(a.drawingEntitiesManager.molecules.length>0&&a.events.error.dispatch("The molecule will be exported using inline SMILES, and on load will appear as a CHEM monomer"),(0,c.aVK)(Array.from(a.drawingEntitiesManager.monomers.values()),a.monomersLibrary)||a.events.error.dispatch("Some of the monomers do not have aliases in the HELM Core Library - they are exported using Ketcher aliases."));try{if(g(!0),("fasta"===e||"sequence"===e||"idt"===e)&&!a.drawingEntitiesManager.validateIfApplicableForFasta())throw new Error("Error during sequence type recognition(RNA, DNA or Peptide)");const o=Cl(e),n=await r.convert({struct:t,output_format:kl[e]},o.options);h(n.struct)}catch(n){let e;e=n instanceof Error?n.message:"string"===typeof n?n:JSON.stringify(n);const t="Convert error! "+e;o(Fo(t)),c.$yr.error(t),l("ket")}finally{g(!1)}}else h(t)})(e),customStylesForExpanded:Hs,testId:"file-format-list"})]}),b?(0,n.Y)(Vs,{dangerouslySetInnerHTML:{__html:b},"data-testid":"preview-area"}):(0,n.FD)(Ws,{children:[(0,n.Y)(Ss,{testId:"preview-area",value:u,readonly:!0}),(0,n.Y)(f.K0,{onClick:e=>{e.preventDefault(),(0,c.ulQ)()?navigator.clipboard.writeText(u).catch(e=>{c.$yr.error("copyAs.js::copyAs",e),o(Fo("This feature is not available in your browser"))}):o(Fo("This feature is not available in your browser"))},iconName:"copy",title:"Copy to clipboard",testId:"copy-to-clipboard"}),m&&(0,n.Y)(qs,{children:(0,n.Y)(ls,{})})]})]})}),(0,n.FD)(wa.Footer,{children:[(0,n.Y)(ka,{label:"Cancel",styleType:"secondary",clickHandler:e,"data-testid":"cancel-button"}),(0,n.Y)(ka,{label:"Save",clickHandler:y,disabled:!d,"data-testid":"save-button"})]})]})},delete:({isModalOpen:e,onClose:t})=>{const o=Lr(),r=Dr(mn),a=Dr(kt),s=(0,i.useCallback)(()=>{t()},[t]);return(0,n.FD)(wa,{isOpen:e,title:"Delete RNA Preset",onClose:s,testId:"delete-preset-modal",children:[(0,n.Y)(wa.Content,{children:(0,n.FD)(Yl,{"data-testid":"delete-preset-popup-content",children:[(0,n.Y)("div",{children:"You are about to delete"}),(0,n.FD)("div",{children:['"',r.name,'" RNA preset.']}),(0,n.Y)("div",{children:"This operation cannot be undone."})]})}),(0,n.FD)(wa.Footer,{children:[(0,n.Y)(Sl,{clickHandler:()=>{s()},label:"Cancel",styleType:"secondary","data-testid":"cancel-delete-preset-button"},"cancel"),(0,n.Y)(Sl,{clickHandler:()=>{s(),o(Dn(r)),o(Fn(!1)),o(En()),a?.events.selectPreset.dispatch(null)},label:"Delete","data-testid":"delete-preset-button"},"delete")]})]})},updateSequenceInRNABuilder:({isModalOpen:e,onClose:t})=>{const o=Lr(),r=Dr(tn),a=Dr(kt),s=Zi(r),l=(0,i.useCallback)(()=>{t()},[t]);return(0,n.FD)(wa,{isOpen:e,title:"Update sequence",onClose:l,testId:"update-sequence-modal",children:[(0,n.Y)(wa.Content,{"data-testid":"update-sequence-modal-body",children:(0,n.FD)(Ll,{children:["You are going to modify ",s," nucleotides. Are you sure?"]})}),(0,n.FD)(wa.Footer,{children:[(0,n.Y)(ka,{clickHandler:()=>{l()},label:"Cancel",styleType:"secondary",title:"","data-testid":"update-sequence-cancel-button"},"cancel"),(0,n.Y)(ka,{clickHandler:()=>{l(),a?.events.modifySequenceInRnaBuilder.dispatch(r),Qi(o,a)},label:"Yes",title:"","data-testid":"update-sequence-yes-button"},"update")]})]})},monomerConnection:({onClose:e,isModalOpen:t,firstMonomer:o,secondMonomer:r,polymerBond:a,isReconnectionDialog:s})=>{const l=Dr(kt),[d]=(0,i.useState)(()=>({firstAttachmentPoint:a?.firstMonomerAttachmentPoint,secondAttachmentPoint:a?.secondMonomerAttachmentPoint,hasFreeAttachmentPoints:o?.hasFreeAttachmentPoint||r?.hasFreeAttachmentPoint}));if(!o||!r)throw new Error("Monomers must exist!");const[c,p]=(0,i.useState)(d.firstAttachmentPoint||Xl(o)),[u,h]=(0,i.useState)(d.secondAttachmentPoint||Xl(r)),[m,g]=(0,i.useState)(!1),f=()=>{s?(a?.firstMonomer.setBond(d.firstAttachmentPoint,a),a?.secondMonomer?.setBond(d.secondAttachmentPoint,a),e()):(l?.events.cancelBondCreationViaModal.dispatch(r),e())};return(0,n.FD)(Ul,{title:s?"Edit Attachment Points":"Select Attachment Points",isOpen:t,onClose:f,showExpandButton:!0,modalWidth:"358px",expanded:m,setExpanded:g,testId:"monomer-connection-modal",children:[(0,n.Y)(wa.Content,{children:(0,n.Y)(Fl,{children:(0,n.Y)(Wl,{firstMonomer:o,secondMonomer:r,expanded:m,firstMonomerOverview:(0,n.Y)(Zl,{monomer:o,selectedAttachmentPoint:c,onSelectAttachmentPoint:p,expanded:m,position:"left"}),secondMonomerOverview:(0,n.Y)(Zl,{monomer:r,selectedAttachmentPoint:u,onSelectAttachmentPoint:h,expanded:m,position:"right"})})})}),(0,n.FD)(wa.Footer,{children:[(0,n.Y)(Kl,{label:"Cancel","data-testid":"cancel-button",styleType:"secondary",clickHandler:f}),(0,n.Y)(Gl,{label:s?"Reconnect":"Connect","data-testid":s?"Reconnect-button":"Connect-button",disabled:!c||!u||!d.hasFreeAttachmentPoints,clickHandler:()=>{if(!c||!u)throw new Error("Attachment points cannot be falsy");c!==d.firstAttachmentPoint||u!==d.secondAttachmentPoint?(l?.events.createBondViaModal.dispatch({firstMonomer:o,secondMonomer:r,firstSelectedAttachmentPoint:c,secondSelectedAttachmentPoint:u,polymerBond:a,isReconnection:s,initialFirstMonomerAttachmentPoint:d.firstAttachmentPoint,initialSecondMonomerAttachmentPoint:d.secondAttachmentPoint}),e()):f()}})]})]})},confirmationDialog:({title:e,confirmationText:t,onConfirm:o,isModalOpen:r,onClose:i})=>(0,n.FD)(wa,{isOpen:r,title:e??"Confirm your action",onClose:i,testId:"confirmation-dialog",children:[(0,n.Y)(wa.Content,{children:(0,n.Y)(Jl,{"data-testid":"confirmation-text",children:t})}),(0,n.FD)(wa.Footer,{children:[(0,n.Y)(ka,{label:"Cancel",clickHandler:i,"data-testid":"cancel-button"}),(0,n.Y)(ka,{label:"Yes",clickHandler:()=>{o?.(),i()},styleType:"secondary","data-testid":"yes-button"})]})]}),settings:({isModalOpen:e,onClose:t})=>{const o=window.ketcher?.settingsService,[r,a]=(0,i.useState)(null),[s,l]=(0,i.useState)(null),[d,p]=(0,i.useState)(["general"]),[u,h]=(0,i.useState)(!1);(0,i.useEffect)(()=>{if(!o||!e)return;const t=o.getSettings(),n=(0,c.CFh)(t);return a(n),l(n),o.subscribe(e=>{const t=(0,c.CFh)(e);a(t)})},[o,e]);const m=(0,i.useMemo)(()=>!(!r||!s)&&JSON.stringify(r)!==JSON.stringify(s),[r,s]),g=()=>{t()};if(!r)return null;const b=(0,n.FD)(nd,{children:[(0,n.Y)(rd,{children:"Settings"}),(0,n.Y)(id,{onClick:()=>{const e=document.createElement("input");e.type="file",e.accept=".json",e.onchange=async e=>{const t=e.target.files?.[0];if(t&&o){h(!0);try{const e=await t.text();await o.importSettings(e)}catch(n){c.$yr.error("Failed to import settings:",n),alert(`Import failed: ${n instanceof Error?n.message:String(n)}`)}finally{h(!1)}}},e.click()},disabled:u,title:"Open from File","data-testid":"open-settings-from-file-button",children:(0,n.Y)(f.In,{name:"open-1"})}),(0,n.Y)(id,{onClick:()=>{if(o)try{const e=o.exportSettings(),t=new Blob([e],{type:"application/json"}),n=URL.createObjectURL(t),r=document.createElement("a");r.href=n,r.download=`ketcher-settings-${Date.now()}.json`,r.click(),URL.revokeObjectURL(n)}catch(e){c.$yr.error("Failed to export settings:",e),alert("Export failed")}},disabled:u,title:"Save to File","data-testid":"save-settings-to-file-button",children:(0,n.Y)(f.In,{name:"save-1"})}),(0,n.Y)(id,{onClick:async()=>{if(o){h(!0);try{const e=await o.resetToDefaults(),t=(0,c.CFh)(e);a(t),l(t)}catch(e){c.$yr.error("Failed to reset settings:",e)}finally{h(!1)}}},disabled:u,title:"Reset","data-testid":"reset-settings-button",children:(0,n.Y)(f.In,{name:"reset"})})]});return(0,n.FD)(wa,{title:b,isOpen:e,onClose:g,showExpandButton:!0,testId:"settings-modal",children:[(0,n.Y)(wa.Content,{children:(0,n.Y)(od,{children:(0,n.Y)(md,{settings:r,onChange:e=>{a(t=>t?{...t,...e}:null)},expandedGroups:d,onGroupToggle:e=>{p(t=>t.includes(e)?t.filter(t=>t!==e):[...t,e])}})})}),(0,n.FD)(wa.Footer,{children:[(0,n.Y)(ad,{children:(0,n.Y)(ka,{label:"Set ACS Settings",styleType:"secondary",clickHandler:async()=>{if(o){h(!0);try{await o.loadPreset("acs");const e=o.getSettings(),t=(0,c.CFh)(e);a(t)}catch(e){c.$yr.error("Failed to apply ACS style:",e)}finally{h(!1)}}},disabled:u,"data-testid":"acs-style-button"})}),(0,n.FD)(sd,{children:[(0,n.Y)(ka,{label:"Cancel",styleType:"secondary",clickHandler:g,disabled:u}),(0,n.Y)(ka,{label:"Apply",clickHandler:async()=>{if(o&&r){h(!0);try{const e=(0,c.dPT)(r);await o.updateSettings(e),l(r),t()}catch(e){c.$yr.error("Failed to save settings:",e)}finally{h(!1)}}},disabled:!m||u})]})]})]})}},fd=()=>{const e=Dr(Bo),t=Dr(Po),o=Dr(To),r=Lr(),a=(0,i.useCallback)(()=>{r(Lo())},[r]);if(!t)return null;const s=gd[t];if(!s)throw new Error(`There is no modal window named ${t}`);return o?(0,n.Y)(s,{onClose:a,isModalOpen:e,...o}):(0,n.Y)(s,{onClose:a,isModalOpen:e})},bd=g.A.div({display:"flex",flexDirection:"column",gap:"8px"}),xd=g.A.div({backgroundColor:"#333333",maxHeight:"40px",display:"flex",alignItems:"stretch"}),yd=g.A.div({maxWidth:"356px",padding:"4px 10px 4px 10px",color:"white",display:"flex"}),vd=(0,g.A)(f.K0)`
  color: white;
  width: 24px;
  height: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  &:hover {
    background-color: #585858;
    color: white;
  }

  svg {
    width: 16px;
    height: 16px;
  }
`,wd=()=>(0,n.FD)(n.FK,{children:[(0,n.Y)("symbol",{id:"chem",viewBox:"0 0 59 59",width:"59",height:"59",children:(0,n.Y)("rect",{className:"monomer-body",width:"29.5",height:"29.5","data-actual-width":"29.5","data-actual-height":"29.5",x:"0.5",y:"0.5",rx:"0.75"})}),(0,n.Y)("symbol",{id:"chem-selection",viewBox:"0 0 59 59",width:"59",height:"59",children:(0,n.Y)("rect",{width:"29.5",height:"29.5",x:"0.5",y:"0.5",rx:"0.75",stroke:"#0097A8",fill:"none",strokeWidth:"1.5"})}),(0,n.FD)("symbol",{id:"chem-autochain-preview",viewBox:"0 0 32 32",width:"32",height:"32","data-actual-width":"32","data-actual-height":"32",children:[(0,n.Y)("path",{d:"M1 4.63574V2.63574C1 1.53117 1.89543 0.635742 3 0.635742H5"}),(0,n.Y)("path",{d:"M1 26.6357V28.6357C1 29.7403 1.89543 30.6357 3 30.6357H5"}),(0,n.Y)("path",{d:"M31 4.63574V2.63574C31 1.53117 30.1046 0.635742 29 0.635742H27"}),(0,n.Y)("path",{d:"M31 26.6357V28.6357C31 29.7403 30.1046 30.6357 29 30.6357H27"}),(0,n.Y)("path",{d:"M7.02142 30.6357L10.0214 30.6357"}),(0,n.Y)("path",{d:"M12.0013 30.6357L15.0013 30.6357"}),(0,n.Y)("path",{d:"M16.9816 30.6357L19.9816 30.6357"}),(0,n.Y)("path",{d:"M22.0222 30.6357L25.0222 30.6357"}),(0,n.Y)("path",{d:"M1.02185 6.63574L1.02185 9.63574"}),(0,n.Y)("path",{d:"M1.02185 11.6152L1.02185 14.6152"}),(0,n.Y)("path",{d:"M1.02185 16.5957L1.02185 19.5957"}),(0,n.Y)("path",{d:"M1.02185 21.6367L1.02185 24.6367"}),(0,n.Y)("path",{d:"M7.02142 0.635742L10.0214 0.635742"}),(0,n.Y)("path",{d:"M12.0013 0.635742L15.0013 0.635742"}),(0,n.Y)("path",{d:"M16.9816 0.635742L19.9816 0.635742"}),(0,n.Y)("path",{d:"M22.0222 0.635742L25.0222 0.635742"}),(0,n.Y)("path",{d:"M31.0219 6.63574L31.0219 9.63574"}),(0,n.Y)("path",{d:"M31.0219 11.6152L31.0219 14.6152"}),(0,n.Y)("path",{d:"M31.0219 16.5957L31.0219 19.5957"}),(0,n.Y)("path",{d:"M31.0219 21.6367L31.0219 24.6367"})]})]}),Ad=()=>(0,n.FD)(n.FK,{children:[(0,n.Y)("symbol",{id:"peptide",viewBox:"0 0 70 61",width:"70",height:"61",children:(0,n.Y)("path",{className:"monomer-body",transform:"scale(0.5)","data-actual-width":"35","data-actual-height":"30.5",d:"M16.9236 1.00466C17.2801 0.383231 17.9418 6.10888e-07 18.6583 5.98224e-07L51.3417 2.04752e-08C52.0582 7.81036e-09 52.7199 0.383234 53.0764 1.00466L69.4289 29.5047C69.7826 30.1211 69.7826 30.8789 69.4289 31.4953L53.0764 59.9953C52.7199 60.6168 52.0582 61 51.3417 61H18.6583C17.9418 61 17.2801 60.6168 16.9236 59.9953L0.571095 31.4953C0.217407 30.8789 0.217408 30.1211 0.571096 29.5047L16.9236 1.00466Z"})}),(0,n.FD)("symbol",{id:"peptide-hover",viewBox:"0 0 70 61",width:"70",height:"61",children:[(0,n.Y)("path",{d:"M18.2246 1.75116C18.3137 1.59581 18.4792 1.5 18.6583 1.5L51.3417 1.5C51.5208 1.5 51.6863 1.59581 51.7754 1.75116L53.06 1.01408L51.7754 1.75117L68.1279 30.2512C68.2163 30.4053 68.2163 30.5947 68.1279 30.7488L51.7754 59.2488C51.6863 59.4042 51.5208 59.5 51.3417 59.5H18.6583C18.4792 59.5 18.3137 59.4042 18.2246 59.2488L1.87215 30.7488C1.78372 30.5947 1.78372 30.4053 1.87215 30.2512L18.2246 1.75116Z",fill:"none",transform:"scale(0.5)",stroke:"#0097A8",strokeWidth:"3"})," "]}),(0,n.Y)("symbol",{id:"modified-background",viewBox:"0 0 60 20",width:"30",height:"20",x:"2.5",y:"5",children:(0,n.Y)("path",{xmlns:"http://www.w3.org/2000/svg",d:"M6.52702 20C5.81057 20 5.14885 19.6168 4.79229 18.9953L0.570974 11.6382C0.217285 11.0218 \n           0.217285 10.2639 0.570974 9.64751L5.52999 1.00466C5.88654 0.383235 6.54827 0 7.26472 0H52.735C53.4515\n           0 54.1132 0.383234 54.4698 1.00466L59.4288 9.64751C59.7825 10.2639 59.7825 11.0218 59.4288 11.6382\n           L55.2075 18.9953C54.8509 19.6168 54.1892 20 53.4727 20H6.52702Z",fillOpacity:"0.6"})}),(0,n.FD)("symbol",{id:"peptide-autochain-preview",viewBox:"0 0 37 32",width:"37",height:"32","data-actual-width":"37","data-actual-height":"32",children:[(0,n.Y)("path",{d:"M8.27143 3.53027L9.29272 1.77462C9.65083 1.15901 10.3093 0.780274 11.0215 0.780274L13.1642 0.780274M29.2131 3.53027L28.1919 1.77462C27.8337 1.15901 27.1753 0.780273 26.4631 0.780273L24.3164 0.780273M2.45429 13.5303L1.585 15.0246C1.22337 15.6463 1.22337 16.4143 1.585 17.0359L2.45429 18.5303M29.0386 28.5303L28.0233 30.2756C27.6619 30.897 26.991 31.2803 26.2646 31.2803L24.1459 31.2803M8.09693 28.5303L9.11221 30.2756C9.47371 30.897 10.1446 31.2803 10.871 31.2803H12.9936M34.8558 18.5303L35.7311 17.0256C36.0896 16.4092 36.0896 15.6514 35.7311 15.0349L34.8558 13.5303"}),(0,n.Y)("path",{d:"M14.8937 0.780178L17.8937 0.780178"}),(0,n.Y)("path",{d:"M19.597 0.780178L22.597 0.780178"}),(0,n.Y)("path",{d:"M14.8937 31.2804H17.8937"}),(0,n.Y)("path",{d:"M19.597 31.2804H22.597"}),(0,n.Y)("path",{d:"M3.61253 11.7412L5.11253 9.14309"}),(0,n.Y)("path",{d:"M5.96421 7.66792L7.46421 5.06984"}),(0,n.Y)("path",{d:"M30.1034 26.877L31.6034 24.2789"}),(0,n.Y)("path",{d:"M32.455 22.8038L33.955 20.2057"}),(0,n.Y)("path",{d:"M7.32633 26.9903L5.82633 24.3922"}),(0,n.Y)("path",{d:"M4.97465 22.917L3.47465 20.319"}),(0,n.Y)("path",{d:"M33.9528 11.8759L32.4782 9.26339"}),(0,n.Y)("path",{d:"M31.6409 7.78023L30.1662 5.16769"})]})]}),Cd=()=>(0,n.FD)(n.FK,{children:[(0,n.Y)("symbol",{id:"sugar",viewBox:"0 0 70 70",width:"70",height:"70",children:(0,n.Y)("rect",{className:"monomer-body",width:"28.5",height:"28.5","data-actual-width":"28.5","data-actual-height":"28.5",rx:"5"})}),(0,n.Y)("symbol",{id:"sugar-selection",viewBox:"-1 -1 100 100",width:"70",height:"70",children:(0,n.Y)("rect",{width:"39",height:"39",rx:"5",fill:"none",stroke:"#0097A8",strokeWidth:"2.5"})}),(0,n.Y)("symbol",{id:"sugar-variant",viewBox:"0 0 72 72",width:"70",height:"70",children:(0,n.Y)("rect",{className:"monomer-body",width:"28.5",height:"28.5","data-actual-width":"28.5","data-actual-height":"28.5",x:"0.5",y:"0.5",rx:"5",stroke:"#585858",strokeWidth:"0.5"})}),(0,n.Y)("symbol",{id:"sugar-modified-background",viewBox:"0 0 28 10",width:"27",height:"10",x:"0.7",y:"10",children:(0,n.Y)("path",{d:"M1.5 10C0.947715 10 0.5 9.55229 0.5 9V1C0.5 0.447715 0.947715 0 1.5 0H26.5C27.0523 0 27.5 0.447715 27.5 1V9C27.5 9.55228 27.0523 10 26.5 10H1.5Z",fillOpacity:"0.6"})}),(0,n.Y)("symbol",{id:"sugar-modified-background-variant",viewBox:"0 0 28 10",width:"27",height:"10",x:"0.7",y:"10",children:(0,n.Y)("path",{d:"M1.5 10C0.947715 10 0.5 9.55229 0.5 9V1C0.5 0.447715 0.947715 0 1.5 0H26.5C27.0523 0 27.5 0.447715 27.5 1V9C27.5 9.55228 27.0523 10 26.5 10H1.5Z",fill:"#333333",fillOpacity:"0.6"})}),(0,n.FD)("symbol",{id:"sugar-autochain-preview",viewBox:"0 0 32 32",width:"32",height:"32","data-actual-width":"32","data-actual-height":"32",children:[(0,n.Y)("path",{d:"M1 4.63574V2.63574C1 1.53117 1.89543 0.635742 3 0.635742H5"}),(0,n.Y)("path",{d:"M1 26.6357V28.6357C1 29.7403 1.89543 30.6357 3 30.6357H5"}),(0,n.Y)("path",{d:"M31 4.63574V2.63574C31 1.53117 30.1046 0.635742 29 0.635742H27"}),(0,n.Y)("path",{d:"M31 26.6357V28.6357C31 29.7403 30.1046 30.6357 29 30.6357H27"}),(0,n.Y)("path",{d:"M7.02142 30.6357L10.0214 30.6357"}),(0,n.Y)("path",{d:"M12.0013 30.6357L15.0013 30.6357"}),(0,n.Y)("path",{d:"M16.9816 30.6357L19.9816 30.6357"}),(0,n.Y)("path",{d:"M22.0222 30.6357L25.0222 30.6357"}),(0,n.Y)("path",{d:"M1.02185 6.63574L1.02185 9.63574"}),(0,n.Y)("path",{d:"M1.02185 11.6152L1.02185 14.6152"}),(0,n.Y)("path",{d:"M1.02185 16.5957L1.02185 19.5957"}),(0,n.Y)("path",{d:"M1.02185 21.6367L1.02185 24.6367"}),(0,n.Y)("path",{d:"M7.02142 0.635742L10.0214 0.635742"}),(0,n.Y)("path",{d:"M12.0013 0.635742L15.0013 0.635742"}),(0,n.Y)("path",{d:"M16.9816 0.635742L19.9816 0.635742"}),(0,n.Y)("path",{d:"M22.0222 0.635742L25.0222 0.635742"}),(0,n.Y)("path",{d:"M31.0219 6.63574L31.0219 9.63574"}),(0,n.Y)("path",{d:"M31.0219 11.6152L31.0219 14.6152"}),(0,n.Y)("path",{d:"M31.0219 16.5957L31.0219 19.5957"}),(0,n.Y)("path",{d:"M31.0219 21.6367L31.0219 24.6367"})]})]}),Md=()=>(0,n.FD)(n.FK,{children:[(0,n.Y)("symbol",{id:"phosphate",viewBox:"0 0 70 70",width:"70",height:"70",children:(0,n.Y)("rect",{className:"monomer-body",width:"28",height:"28","data-actual-width":"28","data-actual-height":"28",rx:"15"})}),(0,n.Y)("symbol",{id:"phosphate-selection",viewBox:"-1 -1 75 75",width:"70",height:"70",children:(0,n.Y)("rect",{width:"28",height:"28",rx:"15",fill:"none",stroke:"#0097A8",strokeWidth:"1.5"})}),(0,n.Y)("symbol",{id:"phosphate-variant",viewBox:"0 0 70 70",width:"70",height:"70",children:(0,n.Y)("rect",{className:"monomer-body",width:"27",height:"27","data-actual-width":"27","data-actual-height":"27",stroke:"#585858",strokeWidth:"0.5",x:"0.5",y:"0.5",rx:"15"})}),(0,n.Y)("symbol",{id:"phosphate-modified-background",viewBox:"0 0 28 10",width:"26",height:"10",x:"1",y:"9.5",children:(0,n.Y)("path",{d:"M2.13388 0C1.72465 0 1.35333 0.248229 1.22109 0.635509C0.753639 2.00455 0.5 3.47265 0.5 5C0.5 6.52735 0.753639 7.99545 1.22109 9.36449C1.35333 9.75177 1.72465 10 2.13389 10H25.8661C26.2753 10 26.6467 9.75177 26.7789 9.36449C27.2464 7.99545 27.5 6.52735 27.5 5C27.5 3.47265 27.2464 2.00455 26.7789 0.635509C26.6467 0.248229 26.2753 0 25.8661 0H2.13388Z",fill:"#333333",fillOpacity:"0.6"})}),(0,n.Y)("symbol",{id:"phosphate-autochain-preview",viewBox:"-1 -1 30 30",width:"30",height:"30","data-actual-width":"30","data-actual-height":"30",children:(0,n.Y)("rect",{width:"28",height:"28",rx:"15",strokeDasharray:"4 4"})})]}),kd=()=>(0,n.FD)(n.FK,{children:[(0,n.Y)("symbol",{id:"rna-base",viewBox:"-16 0 65 65",width:"65",height:"95",children:(0,n.Y)("rect",{width:"22.5",height:"22.5","data-actual-width":"31.82","data-actual-height":"31.82",rx:"1",x:"-11.25",y:"-11.25",transform:"rotate(45)",className:"monomer-body"})}),(0,n.Y)("symbol",{id:"rna-base-selection",viewBox:"-15.75 -0.25 65 65",width:"65",height:"95",children:(0,n.Y)("rect",{width:"21",height:"21",rx:"1",x:"-10.5",y:"-10.5",transform:"rotate(45)",stroke:"#0097A8",strokeWidth:"1.5",fill:"none"})}),(0,n.Y)("symbol",{id:"rna-base-variant",viewBox:"-16 0 65 65",width:"65",height:"94",children:(0,n.Y)("rect",{width:"21.5",height:"21.5","data-actual-width":"30","data-actual-height":"30",stroke:"#585858",strokeWidth:"0.5",rx:"1",x:"-10.25",y:"-10.25",transform:"rotate(45)",className:"monomer-body"})}),(0,n.Y)("symbol",{id:"rna-base-modified-background",viewBox:"0 0 32 10",width:"30",height:"9",x:"0.8",y:"10.8",children:(0,n.Y)("path",{fillRule:"evenodd",clipRule:"evenodd",d:"M6.30156 10C6.03634 10 5.78199 9.89464 5.59445 9.70711L1.1508 5.26346C0.760279 4.87293 0.760279 4.23977 1.1508 3.84924L4.70715 0.292893C4.89469 0.105357 5.14904 0 5.41426 0H26.5858C26.851 0 27.1054 0.105357 27.2929 0.292893L30.8493 3.84924C31.2398 4.23977 31.2398 4.87293 30.8493 5.26346L26.4056 9.70711C26.2181 9.89464 25.9637 10 25.6985 10H6.30156Z",fill:"#333333",fillOpacity:"0.6"})}),(0,n.FD)("symbol",{id:"rna-base-autochain-preview",viewBox:"0 0 36 36",width:"36",height:"36","data-actual-width":"36","data-actual-height":"36",children:[(0,n.Y)("path",{d:"M2.77636 20.2837L1.83359 19.341C1.05254 18.5599 1.05254 17.2936 1.83359 16.5125L2.77468 15.5715"}),(0,n.Y)("path",{d:"M15.7398 33.2465L16.6826 34.1893C17.4636 34.9704 18.73 34.9704 19.511 34.1893L20.4521 33.2482"}),(0,n.Y)("path",{d:"M20.4412 2.61969L19.4984 1.67691C18.7173 0.89586 17.451 0.895861 16.67 1.67691L15.7289 2.618"}),(0,n.Y)("path",{d:"M33.4046 15.5825L34.3474 16.5252C35.1284 17.3063 35.1284 18.5726 34.3474 19.3537L33.4063 20.2948"}),(0,n.Y)("path",{d:"M21.6425 32.0586L23.4089 30.2921"}),(0,n.Y)("path",{d:"M24.5747 29.126L26.3412 27.3595"}),(0,n.Y)("path",{d:"M27.5073 26.1934L29.2737 24.4269"}),(0,n.Y)("path",{d:"M30.4753 23.2256L32.2418 21.4591"}),(0,n.Y)("path",{d:"M3.96771 21.4492L5.73546 23.217"}),(0,n.Y)("path",{d:"M6.90216 24.3838L8.6699 26.1515"}),(0,n.Y)("path",{d:"M9.83673 27.3184L11.6045 29.0861"}),(0,n.Y)("path",{d:"M12.8069 30.2881L14.5747 32.0558"}),(0,n.Y)("path",{d:"M3.96497 14.3809L5.73145 12.6144"}),(0,n.Y)("path",{d:"M6.89728 11.4482L8.66376 9.68176"}),(0,n.Y)("path",{d:"M9.82977 8.5166L11.5963 6.75012"}),(0,n.Y)("path",{d:"M12.7979 5.54785L14.5643 3.78137"}),(0,n.Y)("path",{d:"M21.6326 3.78418L23.4003 5.55192"}),(0,n.Y)("path",{d:"M24.567 6.71875L26.3347 8.48649"}),(0,n.Y)("path",{d:"M27.5016 9.65332L29.2693 11.4211"}),(0,n.Y)("path",{d:"M30.4718 12.623L32.2395 14.3908"})]})]}),Id=()=>(0,n.FD)(n.FK,{children:[(0,n.FD)("symbol",{id:"unresolved-monomer",viewBox:"0 0 59 59",width:"59",height:"59",children:[(0,n.Y)("rect",{className:"monomer-body",width:"29.5",height:"29.5","data-actual-width":"29.5","data-actual-height":"29.5",x:"0.5",y:"0.5",rx:"1.5",stroke:"#333333"}),(0,n.Y)("rect",{width:"29.5",height:"29.5","data-actual-width":"29.5","data-actual-height":"29.5",x:"0.5",y:"0.5",rx:"1.5",fill:"#585858"})]}),(0,n.Y)("symbol",{id:"unresolved-monomer-hover",children:(0,n.Y)("rect",{width:"29.5",height:"29.5",x:"0.5",y:"0.5",rx:"1.5",fill:"none",stroke:"#0097A8",strokeWidth:"1.5"})}),(0,n.FD)("symbol",{id:"unresolved-monomer-autochain-preview",viewBox:"0 0 32 32",width:"32",height:"32","data-actual-width":"32","data-actual-height":"32",children:[(0,n.Y)("path",{d:"M1 4.63574V2.63574C1 1.53117 1.89543 0.635742 3 0.635742H5"}),(0,n.Y)("path",{d:"M1 26.6357V28.6357C1 29.7403 1.89543 30.6357 3 30.6357H5"}),(0,n.Y)("path",{d:"M31 4.63574V2.63574C31 1.53117 30.1046 0.635742 29 0.635742H27"}),(0,n.Y)("path",{d:"M31 26.6357V28.6357C31 29.7403 30.1046 30.6357 29 30.6357H27"}),(0,n.Y)("path",{d:"M7.02142 30.6357L10.0214 30.6357"}),(0,n.Y)("path",{d:"M12.0013 30.6357L15.0013 30.6357"}),(0,n.Y)("path",{d:"M16.9816 30.6357L19.9816 30.6357"}),(0,n.Y)("path",{d:"M22.0222 30.6357L25.0222 30.6357"}),(0,n.Y)("path",{d:"M1.02185 6.63574L1.02185 9.63574"}),(0,n.Y)("path",{d:"M1.02185 11.6152L1.02185 14.6152"}),(0,n.Y)("path",{d:"M1.02185 16.5957L1.02185 19.5957"}),(0,n.Y)("path",{d:"M1.02185 21.6367L1.02185 24.6367"}),(0,n.Y)("path",{d:"M7.02142 0.635742L10.0214 0.635742"}),(0,n.Y)("path",{d:"M12.0013 0.635742L15.0013 0.635742"}),(0,n.Y)("path",{d:"M16.9816 0.635742L19.9816 0.635742"}),(0,n.Y)("path",{d:"M22.0222 0.635742L25.0222 0.635742"}),(0,n.Y)("path",{d:"M31.0219 6.63574L31.0219 9.63574"}),(0,n.Y)("path",{d:"M31.0219 11.6152L31.0219 14.6152"}),(0,n.Y)("path",{d:"M31.0219 16.5957L31.0219 19.5957"}),(0,n.Y)("path",{d:"M31.0219 21.6367L31.0219 24.6367"})]})]}),Sd=()=>(0,n.FD)(n.FK,{children:[(0,n.Y)("symbol",{id:"nucleotide",viewBox:"0 0 84 84",width:"42",height:"42",children:(0,n.Y)("path",{className:"monomer-body","data-actual-width":"42","data-actual-height":"42",d:"M43.0152 71.4019C42.3887 71.771 41.6113 71.771 40.9848 71.4019L9.25584 52.7093C8.50744 52.2684 8.1292 51.3948 8.31974 50.5473L16.6358 13.5613C16.841 12.6485 17.6516 12 18.5871 12L65.4129 12C66.3484 12 67.159 12.6485 67.3642 13.5613L75.6803 50.5473C75.8708 51.3948 75.4926 52.2684 74.7442 52.7093L43.0152 71.4019Z",transform:"rotate(180, 42, 42)"})}),(0,n.Y)("symbol",{id:"nucleotide-hover",viewBox:"0 0 84 84",width:"42",height:"42",children:(0,n.Y)("path",{"data-actual-width":"42","data-actual-height":"42",d:"M43.0152 71.4019C42.3887 71.771 41.6113 71.771 40.9848 71.4019L9.25584 52.7093C8.50744 52.2684 8.1292 51.3948 8.31974 50.5473L16.6358 13.5613C16.841 12.6485 17.6516 12 18.5871 12L65.4129 12C66.3484 12 67.159 12.6485 67.3642 13.5613L75.6803 50.5473C75.8708 51.3948 75.4926 52.2684 74.7442 52.7093L43.0152 71.4019Z",transform:"rotate(180, 42, 42)",fill:"none",stroke:"#0097A8",strokeWidth:"3"})}),(0,n.FD)("symbol",{id:"nucleotide-autochain-preview",viewBox:"0 0 36 32",width:"36",height:"32","data-actual-width":"36","data-actual-height":"32",children:[(0,n.Y)("path",{d:"M20.3771 2.38574L18.5929 1.0722C18.2402 0.812609 17.7598 0.812609 17.4071 1.0722L15.6229 2.38574M32.6021 11.3857L34.4405 12.7392C34.7769 12.9868 34.9239 13.4174 34.8092 13.8191L34.2725 15.6985M3.39787 11.3857L1.55948 12.7392C1.2231 12.9868 1.07608 13.4174 1.19078 13.8191L1.72751 15.6985M30.8088 27.8266L30.2138 29.9168C30.0914 30.3466 29.6988 30.643 29.252 30.643H27.0063M5.19116 27.8266L5.7931 29.9194C5.91633 30.3479 6.30831 30.643 6.75414 30.643H9.00194"}),(0,n.Y)("path",{d:"M10.5017 30.6426L13.5017 30.6426"}),(0,n.Y)("path",{d:"M16.5032 30.6426L19.5032 30.6426"}),(0,n.Y)("path",{d:"M22.5046 30.6426L25.5046 30.6426"}),(0,n.Y)("path",{d:"M2.23663 17.4971L3.06363 20.3808"}),(0,n.Y)("path",{d:"M3.83911 23.0859L4.66611 25.9697"}),(0,n.Y)("path",{d:"M22.356 3.85059L25.5765 6.22306"}),(0,n.Y)("path",{d:"M27.5099 7.64746L30.7304 10.0199"}),(0,n.Y)("path",{d:"M13.605 3.85156L10.3846 6.22404"}),(0,n.Y)("path",{d:"M8.45105 7.64844L5.23059 10.0209"}),(0,n.Y)("path",{d:"M33.7705 17.4971L32.9435 20.3808"}),(0,n.Y)("path",{d:"M32.168 23.0859L31.341 25.9697"})]})]}),Yd=()=>(0,n.FD)(n.FK,{children:[(0,n.Y)("path",{strokeLinecap:"round",d:"M5,0 0,2.5 5,5z",id:"arrow-marker-content"}),(0,n.Y)("marker",{id:"arrow-marker",markerHeight:"3",markerWidth:"5",orient:"auto",refX:"2.5",refY:"1.5",children:(0,n.Y)("use",{href:"#arrow-marker-content",transform:"rotate(180 2.5 1.5) scale(1,0.6)",strokeWidth:"1.2500",fill:"black",stroke:"none"})}),(0,n.Y)("marker",{id:"arrow-marker-arc",markerWidth:"5",markerHeight:"5",viewBox:"0 0 5 5",refX:"5",refY:"2.5",orient:"auto",children:(0,n.Y)("use",{href:"#arrow-marker-content",transform:"rotate(180, 2.5, 2.5)",fill:"#365CFF",stroke:"none"})})]}),Ld=()=>(0,n.Y)("symbol",{id:"sequence-start-arrow",viewBox:"0 0 65 65",width:"65",height:"95",children:(0,n.Y)("svg",{width:"12",height:"12",viewBox:"0 0 12 12",fill:"none",xmlns:"http://www.w3.org/2000/svg",children:(0,n.Y)("path",{d:"M10.2802 5.09664C10.9756 5.49813 10.9756 6.50187 10.2802 6.90336L2.56467 11.3579C1.86926 11.7594 1 11.2576 1 10.4546V1.54541C1 0.742426 1.86926 0.240558 2.56467 0.642053L10.2802 5.09664Z",stroke:"#7C7C7F"})})}),Dd=g.A.div`
  padding: 12px;
  font-size: 12px;
  color: #000;
`,Ed=()=>{const e=Lr(),t=Dr(Oo),o=Dr(zo)||"Error message",r=""!==t,i=()=>{e(No())};return(0,n.FD)(wa,{isOpen:r,title:o,onClose:i,testId:"info-modal-window",children:[(0,n.Y)(wa.Content,{children:(0,n.Y)(Dd,{"data-testid":"error-message-body",children:t})}),(0,n.Y)(wa.Footer,{children:(0,n.Y)(ka,{label:"Close",clickHandler:i,"data-testid":"info-modal-close"})})]})},Fd=g.A.div(()=>({height:"100%",position:"relative"})),Nd=g.A.div(()=>({display:"flex",alignItems:"center"})),Pd=g.A.div(()=>({background:"","&.toggler-component-wrapper--disabled":{opacity:.4,pointerEvents:"none",cursor:"default"}})),Bd=g.A.svg(()=>({"&.handCursor":{cursor:"\n      url('data:image/svg+xml;base64,PHN2ZwogICAgICAgIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIKICAgICAgICB3aWR0aD0iMjQiCiAgICAgICAgaGVpZ2h0PSIyNCIKICAgICAgICBmaWxsPSJub25lIgogICAgICAgIHZpZXdCb3g9IjAgMCAyNCAyNCI+CiAgICA8cGF0aAogICAgICAgICAgICBmaWxsPSIjMDAwIgogICAgICAgICAgICBkPSJNMjAuNDM0IDcuNjczYy0uMS0uNi0uNzEzLTEuNzMxLTIuMzItMS43MzEtLjEyNiAwLS4yNTIuMDA3LS4zNzcuMDIzdi0uNzlhMS4wMSAxLjAxIDAgMCAwLS4wNDUtLjNjLS4xNzctLjU2NC0uODc2LTEuNjMxLTIuMjYyLTEuNjMxYTIuNTk5IDIuNTk5IDAgMCAwLS42NzYuMDg2IDIuMzk0IDIuMzk0IDAgMCAwLS4xNDYtLjIzYy0uNDUzLS42NDUtMS4xODYtMS0yLjA2My0xLS44NzcgMC0xLjU4NC4zNi0yLjAyNSAxLjAxN2EyLjQ2MSAyLjQ2MSAwIDAgMC0uMzYxLjg1IDIuNjYzIDIuNjYzIDAgMCAwLS40MDgtLjAzYy0xLjQ3NCAwLTIuMTI0IDEuMTUtMi4yNjIgMS43NTdhMS4wMzQgMS4wMzQgMCAwIDAtLjAyNC4yMjJWMTEuMWwtLjEzLS4xYy0uNTg4LS41LTIuMDM0LTEuMTIxLTMuMzkxLjAyOS0xLjM1NyAxLjE1LTEuMDE1IDIuNjY0LS40MzEgMy40NTIuNDUzLjc3MyA0LjUxMiA3LjQyNSAxMC4yMjMgNy40MjUgMy4yNSAwIDQuOTEtMS41MTIgNS43MjktMi43ODFhNi4yOTEgNi4yOTEgMCAwIDAgLjk4LTIuOFY3LjgzOGMuMDAxLS4wNTYtLjAwMy0uMTEtLjAxMS0uMTY1WiIKICAgIC8+CiAgICA8cGF0aAogICAgICAgICAgICBmaWxsPSIjZmZmIgogICAgICAgICAgICBzdHJva2U9IiNmZmYiCiAgICAgICAgICAgIGQ9Ik0xOC40NDggMTYuMjE1YTQuNDc0IDQuNDc0IDAgMCAxLS43IDEuODg3Yy0uODA1IDEuMi0yLjE1MyAxLjgtNC4wMDggMS44LTQuNzY4IDAtOC40ODQtNi40MTItOC41Mi02LjQ3NmEuNjY5LjY2OSAwIDAgMC0uMDktLjEyNmMtLjI5NC0uNDA4IDAtLjY1NS4xMDctLjc0OC4yNjctLjIyNy41NC0uMjM3LjgxLS4wMzFsMS43NyAxLjQ1MmExIDEgMCAwIDAgMS42NDQtLjc2M1Y2LjA4YS4yODcuMjg3IDAgMCAxIC4yOS0uMTQ4LjQ0LjQ0IDAgMCAxIC4zNzIuMTZ2NC43YTEgMSAwIDEgMCAyIDB2LTYuNDVjLjA2OC0uMjQ4LjMtLjI0OC40MjItLjI0OC4yIDAgLjQxNC4wMzIuNDgyLjI1NXY2LjQ0YTEgMSAwIDEgMCAyIDB2LTUuMmEuNzUuNzUgMCAwIDEgLjA3NC0uMTljLjA0OS0uMDgxLjEwOC0uMTUuMzI1LS4xNWEuMzUuMzUgMCAwIDEgLjMwNy4xNXY1LjM5YTEgMSAwIDEgMCAyIDBWOC4wNDdhLjU4My41ODMgMCAwIDEgLjM3Ny0uMWMuMDY1IDAgLjI1OC4wMDYuMzM0LjA3OGwuMDA0IDguMTlaIgogICAgLz4KPC9zdmc+Cg=='),\n      auto\n    "},"&.handCursorGrabbing":{cursor:"\n      url('data:image/svg+xml;base64,PHN2ZwogICAgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIgogICAgd2lkdGg9IjI0IgogICAgaGVpZ2h0PSIyNCIKICAgIGZpbGw9Im5vbmUiCiAgICB2aWV3Qm94PSIwIDAgMzAgMzAiCiAgPgogICAgPHBhdGgKICAgICAgZmlsbD0iI2ZmZiIKICAgICAgZD0iTTEwLjc0NCAzQzguODkgMyA3LjM4NCA0LjUyNSA3LjM4NCA2LjR2LjIyaC0uMDEyYy0xLjk5NyAwLTMuNjIyIDEuNjAyLTMuNjIyIDMuNTY4bC4wMDEgNy4wMmMwIDEuNzcgMS41MTIgMy41MTUgMy4xMTIgNS4zNjEuNzIuODMgMS45MTkgMi4yMTYgMi4wMzQgMi43MzF2MS41MjJoMTMuNDQydi0xLjU1NGMwLS41OS42MTgtMS43NyAxLjE2My0yLjgxLjkzOC0xLjc4OSAxLjk5OC0zLjgxNSAxLjk5OC01Ljk5MSAwLTMuMjU4LS4wMS02LjI0OC0uMDEtNi4yNDggMC0xLjY5Ni0xLjM5LTMuMDgtMy4xLTMuMDgtLjIwMyAwLS40LjAyLS41OTIuMDU3YTMuNjUgMy42NSAwIDAgMC0zLjAzMy0xLjYwN2MtLjM2NyAwLS43Mi4wNTMtMS4wNTUuMTUzYTMuNjM2IDMuNjM2IDAgMCAwLTMuMDktMS43MDVjLS40NDQgMC0uODcuMDgtMS4yNjQuMjI1QTMuMzQ1IDMuMzQ1IDAgMCAwIDEwLjc0NCAzWiIKICAgIC8+CiAgICA8cGF0aAogICAgICBmaWxsPSIjMDAwIgogICAgICBkPSJNMTAuNzQ0IDQuNTU0YzEgMCAxLjgwOC44MjYgMS44MDggMS44NDV2MS4xOTdjMC0xLjEwOS45MjctMi4wMDggMi4wNjgtMi4wMDggMS4xNDMgMCAyLjA2OC45IDIuMDY4IDIuMDA4djEuNTUyYzAtMS4xMS45My0yLjAwOCAyLjA3Ny0yLjAwOCAxLjE0NyAwIDIuMDc2Ljg5OSAyLjA3NiAyLjAwOHYxLjA2OEExLjU0IDEuNTQgMCAwIDEgMjIuMzkgOC42OWMuODU2IDAgMS41NDguNjg0IDEuNTQ4IDEuNTMgMCAwIC4wMSAyLjk4OC4wMSA2LjI0NCAwIDMuMjU3LTMuMTYxIDYuNjM2LTMuMTYxIDguODAySDEwLjQ1YzAtMS45NC01LjE0Ni01Ljc3My01LjE0Ni04LjA2MXYtNy4wMmMwLTEuMTEyLjkyNi0yLjAxNCAyLjA3LTIuMDE1djQuMDUxYzAgLjM0Mi4zNS42Mi43ODEuNjIuNDMyIDAgLjc4Mi0uMjc4Ljc4Mi0uNjJWNi40YzAtMS4wMi44MDktMS44NDUgMS44MDctMS44NDVabTAtMS41NTRDOC44OSAzIDcuMzg0IDQuNTI1IDcuMzg0IDYuNHYuMjJoLS4wMTJjLTEuOTk3IDAtMy42MjIgMS42MDItMy42MjIgMy41NjhsLjAwMSA3LjAyYzAgMS43NyAxLjUxMiAzLjUxNSAzLjExMiA1LjM2MS43Mi44MyAxLjkxOSAyLjIxNiAyLjAzNCAyLjczMXYxLjUyMmgxMy40NDJ2LTEuNTU0YzAtLjU5LjYxOC0xLjc3IDEuMTYzLTIuODEuOTM4LTEuNzg5IDEuOTk4LTMuODE1IDEuOTk4LTUuOTkxIDAtMy4yNTgtLjAxLTYuMjQ4LS4wMS02LjI0OCAwLTEuNjk2LTEuMzktMy4wOC0zLjEtMy4wOC0uMjAzIDAtLjQuMDItLjU5Mi4wNTdhMy42NSAzLjY1IDAgMCAwLTMuMDMzLTEuNjA3Yy0uMzY3IDAtLjcyLjA1My0xLjA1NS4xNTNhMy42MzYgMy42MzYgMCAwIDAtMy4wOS0xLjcwNWMtLjQ0NCAwLS44Ny4wOC0xLjI2NC4yMjVBMy4zNDUgMy4zNDUgMCAwIDAgMTAuNzQ0IDNaIgogICAgLz4KICA8L3N2Zz4='),\n      auto\n    "}}));const Td=g.A.div`
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  width: 100%;
  display: flex;
  justify-content: center;
  align-items: center;
`,Rd=()=>(0,n.Y)(Td,{children:(0,n.Y)(ls,{})}),Od=()=>!!(document.fullscreenElement||document.mozFullScreenElement||document.webkitFullscreenElement||document.msFullscreenElement),zd=g.A.div`
  display: flex;

  & svg:first-of-type {
    display: flex;
    justify-content: center;
    align-items: center;
    width: 24px;
    height: 24px;
    padding: 2px;
    border-radius: 4px;
  }
`,$d=e=>{const[t,o]=(0,i.useState)(Od);(0,i.useEffect)(()=>{const e=()=>o(Od());return document.addEventListener("fullscreenchange",e),document.addEventListener("webkitfullscreenchange",e),document.addEventListener("mozfullscreenchange",e),document.addEventListener("MSFullscreenChange",e),()=>{document.removeEventListener("fullscreenchange",e),document.removeEventListener("webkitfullscreenchange",e),document.removeEventListener("mozfullscreenchange",e),document.removeEventListener("MSFullscreenChange",e)}},[]);return(0,n.Y)(zd,{className:e.className,children:(0,n.Y)(f.K0,{onClick:()=>{const e=(0,f.Kn)();var t;Od()?document.exitFullscreen?document.exitFullscreen():document.msExitFullscreen?document.msExitFullscreen():document.mozCancelFullScreen?document.mozCancelFullScreen():document.webkitExitFullscreen&&document.webkitExitFullscreen():e&&((t=e).requestFullscreen?t.requestFullscreen():t.msRequestFullscreen?t.msRequestFullscreen():t.mozRequestFullScreen?t.mozRequestFullScreen():t.webkitRequestFullscreen&&t.webkitRequestFullscreen())},iconName:t?"fullscreen-exit":"fullscreen-enter",testId:"fullscreen-mode-button"})})},jd=()=>i.useContext(Nr),_d=(0,g.A)(f.K0)`
  margin: 2px;
  outline: none;
`;function Hd(){document.activeElement instanceof HTMLElement&&document.activeElement.blur()}const qd=(0,g.A)(P.A)`
  display: flex;
  justify-content: space-between;
  font-weight: 400;
  font-size: 12px;
  line-height: 14px;
  padding: 7px 8px;
  text-transform: none;
  color: #333333;
  width: max-content;
`,Vd=({itemId:e,title:t="",disabled:o,testId:r,onClick:a,type:s="icon-button"})=>{const{isActive:l,activate:d}=jd(),c=(0,i.useCallback)(()=>{d(e),Hd(),a&&a()},[d,e,a]),p=l(e),u=p?" active":"";return(0,n.Y)(n.FK,{children:"icon-button"===s?(0,n.Y)(_d,{title:t,className:e+u,isActive:p,onClick:c,iconName:e,testId:r,disabled:o}):(0,n.Y)(qd,{title:t,onClick:c,disabled:o,"data-testid":r,children:t})})},Wd=(0,g.A)(f.In,{shouldForwardProp:e=>"isActive"!==e})`
  position: absolute;
  height: 7px;
  width: 7px;
  right: 3px;
  bottom: 3px;
  cursor: pointer;

  path {
    fill: ${({isActive:e})=>e?"white":void 0};
  }
`,Ud=g.A.div`
  display: flex;
  position: relative;
  align-items: center;

  &:active {
    .dropdown {
      fill: white;
    }
  }
`,Kd=g.A.div`
  display: flex;
  border-radius: 4px;
  flex-direction: ${({isVertical:e})=>e?"column":"row"};
  z-index: ${({theme:e})=>e.ketcher.zIndex.overlay};
  background-color: white;
  padding: 2px;
  width: ${({isVertical:e,isAutoSize:t})=>e&&!t?"38px":"auto"};
  height: ${({isVertical:e,isAutoSize:t})=>e||t?"auto":"38px"};
`,Gd=(0,g.A)(B.A)`
  position: absolute;
  z-index: ${({theme:e})=>e.ketcher.zIndex.overlay};
`,Qd=g.A.div`
  display: flex;
  align-items: center;
  position: relative;
  width: 32px;
  height: 32px;
  padding: 0;
  justify-content: center;
  border-radius: 2px;
`,Zd=g.A.div(({theme:e,isHorizontal:t})=>({backgroundColor:e.ketcher.color.background.primary,borderRadius:"4px",display:"flex",flexDirection:t?"row":"column",zIndex:e.ketcher.zIndex.toolbar})),Xd=g.A.hr`
  width: 18px;
  margin: 6px 0;
  align-self: center;
  border-width: thin 0px 0px 0px;
  border-style: solid;
  border-color: ${({theme:e})=>e.ketcher.color.divider};
`,Jd=g.A.hr`
  height: 18px;
  margin: 0px 6px;
  align-self: center;
  border-width: 0px thin 0px 0px;
  border-style: solid;
  border-color: ${({theme:e})=>e.ketcher.color.divider};
`,ec=g.A.div(({theme:e,isHorizontal:t})=>({display:"flex",flexDirection:t?"row":"column",flexWrap:"nowrap",alignItems:"center",backgroundColor:e.ketcher.color.background.primary,borderRadius:"2px",width:t?void 0:"32px",marginBottom:t?void 0:"8px","> :last-child":t?void 0:{marginBottom:0}})),tc=({children:e,divider:t=!1,isHorizontal:o})=>{const r=i.Children.map(e,e=>e);return(0,n.FD)(n.FK,{children:[(0,n.Y)(ec,{isHorizontal:o,children:r}),t&&(o?(0,n.Y)(Jd,{}):(0,n.Y)(Xd,{}))]})},oc=({children:e,onItemClick:t,activeMenuItems:o,testId:r,isHorizontal:a})=>{const s=i.useMemo(()=>({isActive:e=>!!o&&o.includes(e),activate:e=>{t(e)}}),[o,t]),l=i.Children.map(e,e=>e&&e.type===tc?e:null);return(0,n.Y)(Nr.Provider,{value:s,children:(0,n.Y)(Zd,{"data-testid":r,isHorizontal:a,children:l})})};oc.Group=tc,oc.Item=Vd,oc.Submenu=({children:e,vertical:t=!1,autoSize:o=!1,disabled:r=!1,needOpenByMenuItemClick:a=!1,testId:s,layoutModeButton:l=!1,generalTitle:d,activeItem:c,subMenuId:p})=>{const u=(0,i.useRef)(null),[h,m]=(0,i.useState)(!1),{isActive:g}=jd(),[b]=(0,f.dR)([u,h,t,f.tS]),x=Dr((y=p,e=>{if(y)return e.editor.selectedMenuGroupItems[y]}));var y;const v=p?x:null,w=()=>{r||m(e=>!e)},A=()=>{h&&m(!1)},C=i.Children.map(e,e=>e.type===Vd?e:null),M=C.map(e=>e.props.itemId).filter(e=>e),k=M.filter(e=>g(e))[0],I=c??(k||v||M[0]),S=C.find(e=>e.props.itemId===I),Y=S?.props.testId,L=d??S?.props.title,D=document.querySelector(f.tS);return(0,n.Y)(Ud,{"data-testid":s,"data-is-selected":g(I)?"true":"false",ref:u,children:(0,n.FD)(n.FK,{children:[(0,n.FD)(Qd,{children:[(0,n.Y)(Vd,{disabled:r,itemId:I,title:L,testId:Y,onClick:a?w:kr}),h||(0,n.Y)(Wd,{className:"dropdown",name:"dropdown",onClick:w,isActive:g(I),dataTestId:"dropdown-expand"})]}),D&&(0,H.createPortal)((0,n.Y)(Gd,{in:h,timeout:0,style:{...b},unmountOnExit:!0,onClick:A,children:(0,n.Y)(T.x,{onClickAway:A,children:(0,n.Y)(Kd,{isVertical:t,isAutoSize:o,islayoutModeButton:l,"data-testid":"multi-tool-dropdown",children:C.map(e=>i.cloneElement(e,{key:e.props.itemId}))})})}),D)]})})};const nc=()=>{const e=Dr(kt),t=Er(),o=Dr(It),r=(0,i.useMemo)(()=>({isActive:e=>t===e,activate:o=>{o!==t&&(e?.events.selectMode.dispatch(o),e?.events.layoutModeChange.dispatch(o))}}),[t,e]);return(0,n.Y)(Nr.Provider,{value:r,children:(0,n.FD)(oc.Submenu,{disabled:o,testId:"layout-mode",vertical:!0,needOpenByMenuItemClick:!0,layoutModeButton:!0,children:[(0,n.Y)(oc.Item,{itemId:"sequence-layout-mode",testId:"sequence-layout-mode",title:"Switch to sequence layout mode"}),(0,n.Y)(oc.Item,{itemId:"snake-layout-mode",testId:"snake-layout-mode",title:"Switch to snake layout mode"}),(0,n.Y)(oc.Item,{itemId:"flex-layout-mode",testId:"flex-layout-mode",title:"Switch to flex layout mode"})]})})};function rc(e){const{node:t}=e;return t instanceof c.NxW||t instanceof c.yYC||t?.monomer instanceof c.j5f}const ic=e=>{if(!e?.length)return;const t=(0,d.flatten)(e),o=t.length,n=Zi(t);let r,i=!1,a=!0,s=!1,l=!0;const p=(e=>{const t=[];for(const o of e){const{node:e,nodeIndexOverall:n,isNucleosideConnectedAndSelectedWithPhosphate:r,hasR1Connection:i,twoStrandedNode:a}=o,s=Boolean(a?.antisenseNode);e instanceof c.NxW?t.push({type:c.WDE.Nucleotide,baseLabel:e?.rnaBase?.label,sugarLabel:e?.sugar?.label,phosphateLabel:e?.phosphate?.label,rnaBaseMonomerItem:e.rnaBase instanceof c.cN8?e.rnaBase.variantMonomerItem:e.rnaBase.monomerItem,hasR1Connection:i,nodeIndexOverall:n,hasAntisense:s}):e instanceof c.yYC?t.push({type:c.WDE.Nucleoside,baseLabel:e?.rnaBase?.label,sugarLabel:e?.sugar?.label,rnaBaseMonomerItem:e.rnaBase instanceof c.cN8?e.rnaBase.variantMonomerItem:e.rnaBase.monomerItem,isNucleosideConnectedAndSelectedWithPhosphate:r,hasR1Connection:i,nodeIndexOverall:n,hasAntisense:s}):e?.monomer instanceof c.j5f&&t.push({type:c.WDE.Phosphate,phosphateLabel:e?.monomer?.label,nodeIndexOverall:n,hasAntisense:s})}return t})(t);for(let d=0;d<p.length;d++){const e=p[d],t=p[d-1],o=e.type===c.WDE.Nucleotide||e.type===c.WDE.Nucleoside,n=t?.isNucleosideConnectedAndSelectedWithPhosphate;o&&(i=!0),o||n?e.hasR1Connection&&(l=!1):(l=!1,a=!1),e.hasAntisense&&(s=!0)}return o>n&&(t.every(rc)||(a=!1)),r=1===o||1===n&&a?(e=>{let t="";const o=1===e.length?e[0]:(0,d.merge)({},...e);for(const n of["sugarLabel","baseLabel","phosphateLabel"]){const e=(0,d.get)(o,n,"");t+="baseLabel"===n?`(${e})`:e}return t})(p):a?`${n} nucleotides`:`${o} elements`,{title:r,selectedSequenceLabeledNodes:p,isSelectedOnlyNucleoelements:a,isSelectedAtLeastOneNucleoelement:i,isSequenceFirstsOnlyNucleoelementsSelected:l,hasAntisense:s}};function ac(e=[]){return e.every(e=>e.every(e=>(0,c.mI6)(e.twoStrandedNode)))}function sc(e){return e?.monomers.some(e=>0!==e.hydrogenBonds.length)}const lc=e=>{const{monomerItem:t}=e;if("A"===t.props.MonomerNaturalAnalogCode||"C"===t.props.MonomerNaturalAnalogCode||"G"===t.props.MonomerNaturalAnalogCode||"T"===t.props.MonomerNaturalAnalogCode||"U"===t.props.MonomerNaturalAnalogCode)return!0;if(!e.monomerItem.isAmbiguous)return!1;if(e.subtype===c.Mk7.MIXTURE)return!1;const o=["ACGT","ACGU","CGT","CGU","AGT","AGU","ACT","ACU","GT","GU","AT","AU","CT","CU","AC","AG","CG","ACG"],n=e.monomers.map(e=>e.monomerItem.props.MonomerNaturalAnalogCode).sort((e,t)=>e.localeCompare(t)).join("");return o.some(e=>e===n)},dc=e=>e?.some(e=>{const t=e instanceof c.HvV&&(0,c.dJd)(e);return e instanceof c.P5K&&(e.hydrogenBonds.length>0||e.covalentBonds.length>1)||e instanceof c.KQE&&(e.hydrogenBonds.length>0||(o=e,!c.b$S.includes(o.monomerItem.props.MonomerNaturalAnalogCode)))||(0,c.W9x)(e)&&!lc(e)||t&&(t.hydrogenBonds.length>0||t.covalentBonds.length>1||!lc(t));var o}),cc=e=>e?.some(e=>e instanceof c.P5K&&(0,c.TKk)(e)||(0,c.CFG)(e)&&(0,c.dJd)(e)||e instanceof c.KQE),pc=e=>e?.some(e=>e instanceof c.KQE),uc="aminoAcidModification-",hc=e=>{const t=new Set,o=new Set,n=new Map,r=(0,c.uui)();e.forEach(e=>{const t=e.monomerItem.props.MonomerNaturalAnalogCode;e instanceof c.qZ$&&t&&(n.has(t)||n.set(t,[]),n.get(t)?.push(e))}),r?.monomersLibrary.forEach(e=>{const r=n.get(e.props?.MonomerNaturalAnalogCode);if(!e.props||!r)return;const i=e.props.modificationTypes;i&&i.forEach(n=>{if(r.every(t=>t.label===e.label))return;r.some(t=>t.label!==e.label&&(0,c.MEe)(t,e))?t.add(n):o.add(n)})});const i=[...t.values()].filter(e=>!o.has(e)).map(t=>{const o=(0,c.MvC)(e,t,r.monomersLibrary);return{name:`${uc}${t}`,title:t,onMouseOver:()=>{r.transientDrawingView.showModifyAminoAcidsView({monomersToModify:[...o.keys()]}),r.transientDrawingView.update()},onMouseOut:()=>{r.transientDrawingView.hideModifyAminoAcidsView(),r.transientDrawingView.update()}}});return i.sort(c.EtL),i},mc=(e,t)=>{const o=t?.target?.__data__,n=o?.node?.monomer;let r=[];e.length?r=e:n&&(r=[n]);return r.filter(e=>e instanceof c.qZ$)},gc=e=>{if(e.length<3)return!1;const t=new Set(e),o=new Set,n=new Set,r=(e,i)=>{o.add(e),n.add(e);const a=(e=>{const o=[];return e.forEachBond(n=>{if(n instanceof c.t$8)return;const r=n.firstMonomer,i=n.secondMonomer;if(!r||!i)return;const a=n.getAnotherMonomer(e);a&&t.has(a)&&o.push(a)}),o})(e);for(const t of a)if(t!==i){if(n.has(t))return!0;if(!o.has(t)&&r(t,e))return!0}return n.delete(e),!1};for(const i of e)if(!o.has(i)&&r(i,null))return!0;return!1};var fc;!function(e){e.title="sequence_menu_title",e.createRnaAntisenseStrand="create_antisense_rna_chain",e.createDnaAntisenseStrand="create_antisense_dna_chain",e.modifyInRnaBuilder="modify_in_rna_builder",e.modifyAminoAcids="modify_amino_acids",e.establishHydrogenBond="establish_hydrogen_bond",e.deleteHydrogenBond="delete_hydrogen_bond",e.editSequence="edit_sequence",e.startNewSequence="start_new_sequence",e.copy="copy",e.paste="paste",e.delete="delete"}(fc||(fc={}));const bc=({selections:e,contextMenuEvent:t,isPasteAvailable:o=!0})=>{const r=Dr(kt),i=Lr(),a=ic(e),s=e?.flat()?.flatMap(e=>e.node.monomers)||[],l=mc(s,t),d=Dr(It),p="sequence-layout-mode"===Er(),u=hc(l),h=e?.some(e=>e.some(e=>sc(e.node)))??!1,m=s?.length>0&&cc(s),g=!ac(e)||h,b=[{name:fc.title,title:a?.title,isMenuTitle:!0,disabled:!0,hidden:({props:e})=>!e?.sequenceItemRenderer||!a?.isSelectedAtLeastOneNucleoelement},{name:fc.copy,title:"Copy",icon:(0,n.Y)(f.In,{name:"copyMenu"}),disabled:0===s?.length},{name:fc.paste,title:"Paste",icon:(0,n.Y)(f.In,{name:"pasteNavBar"}),disabled:!o,separator:!0},{name:fc.editSequence,title:"Edit sequence",disabled:!1,hidden:({props:e})=>!e?.sequenceItemRenderer},{name:fc.startNewSequence,title:"Start new sequence",disabled:!1,separator:m||g},{name:fc.createRnaAntisenseStrand,title:"Create RNA antisense strand",disabled:dc(s),hidden:()=>!s||!cc(s)},{name:fc.createDnaAntisenseStrand,title:"Create DNA antisense strand",disabled:dc(s),hidden:()=>!s||!cc(s)},{name:fc.establishHydrogenBond,title:"Establish Hydrogen Bonds",disabled:({props:t})=>0===e?.length?(0,c.mI6)(t?.sequenceItemRenderer?.twoStrandedNode):ac(e),hidden:({props:e})=>!e?.sequenceItemRenderer},{name:fc.deleteHydrogenBond,title:"Remove hydrogen bonds",disabled:({props:t})=>0===e?.length?!sc(t?.sequenceItemRenderer?.node):!e?.some(e=>e.some(e=>sc(e.node))),hidden:({props:e})=>!e?.sequenceItemRenderer},{name:fc.modifyInRnaBuilder,title:"Modify in RNA Builder...",disabled:!a?.isSelectedOnlyNucleoelements||a.hasAntisense,hidden:({props:e})=>!e?.sequenceItemRenderer||!a?.isSelectedAtLeastOneNucleoelement},{name:fc.modifyAminoAcids,title:"Modify amino acids",disabled:!1,hidden:!u.length,subMenuItems:u,separator:!0},{name:fc.delete,title:"Delete",disabled:0===s?.length,icon:(0,n.Y)(f.In,{name:"deleteMenu"})}],x=document.querySelector(f.tS);return x&&p&&!d?(0,H.createPortal)((0,n.Y)(ui,{id:li.FOR_SEQUENCE,handleMenuChange:({id:t,props:o})=>{if(r)switch(!0){case t===fc.modifyInRnaBuilder:i(pt(!1)),r.events.turnOnSequenceEditInRNABuilderMode.dispatch(),i(bo(no)),i(Fn(!0)),i(vn({})),i(kn(null)),a?.selectedSequenceLabeledNodes?.length&&a?.title&&(i(An(a?.title)),i(wn(a?.selectedSequenceLabeledNodes)),i(Cn(a?.isSequenceFirstsOnlyNucleoelementsSelected)));break;case t===fc.startNewSequence:r.events.startNewSequence.dispatch(o.sequenceItemRenderer);break;case t===fc.editSequence:r.events.editSequence.dispatch(o.sequenceItemRenderer);break;case t===fc.createRnaAntisenseStrand:r.events.createAntisenseChain.dispatch(!1);break;case t===fc.createDnaAntisenseStrand:r.events.createAntisenseChain.dispatch(!0);break;case t===fc.establishHydrogenBond:r.events.establishHydrogenBond.dispatch(o.sequenceItemRenderer);break;case t===fc.deleteHydrogenBond:{const t=c.HQ1.sequenceViewModel.chainsCollection.monomerToChain,n=new Map;(e?.length?e.reduce((e,t)=>[...e,...t],[]).map(e=>e.twoStrandedNode):[o.sequenceItemRenderer?.twoStrandedNode]).forEach(e=>{if(e.antisenseChain&&e.antisenseNode&&!(e.antisenseNode instanceof c.RR||e.antisenseNode instanceof c.Dzo)){n.has(e.antisenseChain)||n.set(e.antisenseChain,new Set);const t=n.get(e.antisenseChain);if(!t)return;t.add(e)}});let i=!1;n.forEach((e,o)=>{const n=[...e.values()][0].chain,r=new Set([...e.values()].map(e=>e.antisenseNode));o.nodes.some(e=>!r.has(e)&&e.monomers.some(e=>e.hydrogenBonds.some(o=>{const r=o.getAnotherMonomer(e);return(r&&t.get(r))===n})))||(i=!0)}),i?r.events.openConfirmationDialog.dispatch({title:"Deletion of all Hydrogen Bonds",confirmationText:"Deleting all hydrogen bonds will cause the separation of two chains. Do you wish to proceed?",onConfirm:()=>{r.events.deleteHydrogenBond.dispatch(o.sequenceItemRenderer)}}):r.events.deleteHydrogenBond.dispatch(o.sequenceItemRenderer);break}case t?.startsWith(uc):{const e=t?.replace(uc,"");r.events.modifyAminoAcids.dispatch({monomers:l,modificationType:e});break}case"copy"===t:r.events.copySelectedStructure.dispatch();break;case"paste"===t:r.events.pasteFromClipboard.dispatch();break;case"delete"===t:r.events.deleteSelectedStructure.dispatch()}},menuItems:b}),x):null},xc=g.A.div`
  display: flex;
  flex-direction: column;
  align-items: start;
  gap: 8px;
  overflow: hidden;
  width: ${e=>e.isLongName?"450px":"345px"};
  height: 345px;
  background: ${e=>e.theme.ketcher.color.background.primary};
  border: ${e=>e.theme.ketcher.border.regular};
  border-radius: ${e=>e.theme.ketcher.border.radius.regular};
  box-shadow: ${e=>e.theme.ketcher.shadow.regular};
`,yc=g.A.p`
  width: calc(100% - 16px);
  padding: 8px;
  color: ${e=>e.theme.ketcher.color.text.primary};
  background-color: #cceaee;
  font-size: ${e=>e.isLongName?"8px":e.theme.ketcher.font.size.regular};
  font-weight: 700;
  word-break: break-all;
  text-align: left;
  margin: 0;
  white-space: pre-wrap;
  ${e=>e.isLongName&&"\n    max-height: 200px;\n    overflow: hidden;\n    display: -webkit-box;\n    -webkit-line-clamp: 15;\n    -webkit-box-orient: vertical;\n  "}
`,vc=(0,g.A)(f.c9)`
  flex: 1 1 auto;
  min-height: 80px;
  width: 100%;
  padding: 0 8px;
`,wc=g.A.div`
  width: 100%;
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  padding: 0 8px 4px;
`,Ac=({monomerCaps:e,attachmentPointsToBonds:t})=>(0,i.useMemo)(()=>{const o=[],n=[];return e&&Object.keys(e).length>0?(Object.entries(e).forEach(([e,r])=>{const i=Boolean(t?.[e]);i&&n.push(e);const a={id:e,label:r,connected:i};o.push(a)}),{preparedAttachmentPointsData:o,connectedAttachmentPoints:n}):t?(Object.keys(t).forEach(e=>{const r=Boolean(t[e]);r&&n.push(e),o.push({id:e,label:"",connected:r})}),{preparedAttachmentPointsData:o,connectedAttachmentPoints:n}):{preparedAttachmentPointsData:o,connectedAttachmentPoints:n}},[e,t]),Cc=g.A.span`
  width: max-content;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  font-size: ${e=>e.theme.ketcher.font.size.regular};
  font-weight: 500;
  line-height: normal;
  color: ${e=>e.theme.ketcher.color.text.lightgrey};
  padding-left: ${({preset:e})=>e?"0":"8px"};
  border-left: ${({preset:e})=>e?"none":"1px solid #D9DCEA"};
`,Mc=g.A.p`
  margin: 0;
`,kc=g.A.span`
  color: #7c7c7c;
  margin-right: 4px;
`,Ic=g.A.span`
  color: #585858;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
`,Sc=e=>e.replace(/\[|\]/g,"");function Yc({idtAliasesText:e,axoLabsText:t,helmText:o,bilnText:r,modificationTypeText:i,preset:a}){const s=[...e?[{label:"IDT",text:e}]:[],...t?[{label:"AxoLabs",text:t}]:[],...o?[{label:"HELM",text:Sc(o)}]:[],...r?[{label:"BILN",text:Sc(r)}]:[],...i?[{label:"Modification type",text:i}]:[]];return s.length?(0,n.Y)(Cc,{preset:a,children:s.map(e=>(0,n.FD)(Mc,{children:[(0,n.FD)(kc,{children:[e.label,":"]}),(0,n.Y)(Ic,{children:e.text})]},e.label))}):null}const Lc=g.A.div`
  flex: 1;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  line-height: 14px;
`,Dc=g.A.div`
  display: flex;
  align-items: center;
  gap: 2px;
  border-radius: 4px;
  background-color: ${({connected:e})=>e?"#E1E5EA":"transparent"};
  padding: ${({connected:e})=>e?"4px":"0"};
  color: ${({connected:e})=>e?"#8A8B8E":"inherit"};
`,Ec=g.A.span`
  font-weight: 500;
`,Fc=g.A.span`
  font-weight: 600;
`,Nc=({preparedAttachmentPointsData:e})=>e.length?(0,n.Y)(Lc,{children:e.map(({id:e,connected:t,label:o})=>(0,n.FD)(Dc,{connected:t,children:[(0,n.FD)(Ec,{children:[e,!t&&":"]}),!t&&(0,n.Y)(Fc,{children:o})]},e))}):null,Pc=({className:e})=>{const t=Dr(At),{monomer:o,attachmentPointsToBonds:r}=t,a=o?.props.idtAliases,s=o?.props.aliasAxoLabs,l=o?.props.aliasHELM,d=o?.props.aliasBILN,p=o?.props.modificationTypes,{preparedAttachmentPointsData:u,connectedAttachmentPoints:h}=Ac({monomerCaps:o?.props.MonomerCaps,attachmentPointsToBonds:r}),m=(({idtAliases:e,attachmentPointsToBonds:t})=>(0,i.useMemo)(()=>{if(!e)return null;const{base:o,modifications:n}=e;if(!n)return Yr(o);const{endpoint5:r,internal:i,endpoint3:a}=n;if(t){const{R1:e,R2:o}=t,n=null!=e,s=null!=o;return Yr(n&&!s?a??i:!n&&s?r??i:n&&s?i??r??a:r??i??a)}const s=Object.values(n).every(e=>e.includes(o));if(a&&r&&i&&s)return Yr(o);const l={};return Object.values(n).forEach(e=>{const t=Yr(e)??"",[o,n]=[t.charAt(0),t.slice(1)];l[n]=l[n]?[...l[n],o]:[o]}),Object.entries(l).map(([e,t])=>`(${t.join(", ")})${e}`).join(", ")},[e,t]))({idtAliases:a,attachmentPointsToBonds:r});if(!o)return null;const g=o.props.unresolved,f=g?o.label:o.struct?.name||o.label,b=m||s||l||d||p;return(o.struct||g)&&(0,n.FD)(xc,{className:e,isLongName:f.length>100,"data-testid":"polymer-library-preview","data-idtaliases":m??void 0,"data-axolabs":s??void 0,"data-helm":l??void 0,"data-biln":d??void 0,"data-modificationtype":zr(p),children:[f.length>0&&(0,n.Y)(yc,{"data-testid":"preview-tooltip-title",isLongName:f.length>100,children:f}),g?(0,n.Y)(Rl,{}):(0,n.Y)(vc,{struct:o.struct,options:{connectedMonomerAttachmentPoints:h,usageInMacromolecule:c.HT5.MonomerPreview,labelInPreview:!0,needCache:!1}}),(0,n.FD)(wc,{children:[(0,n.Y)(Nc,{preparedAttachmentPointsData:u}),b&&(0,n.Y)(Yc,{idtAliasesText:m??void 0,axoLabsText:s??void 0,helmText:l??void 0,bilnText:d??void 0,modificationTypeText:Array.isArray(p)?p.join(", "):p})]})]})},Bc=g.A.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 8px;
  background: ${e=>e.theme.ketcher.color.background.primary};
  border: ${e=>e.theme.ketcher.border.regular};
  border-radius: ${e=>e.theme.ketcher.border.radius.regular};
  box-shadow: ${e=>e.theme.ketcher.shadow.regular};
`,Tc=g.A.div`
  display: flex;
  align-items: center;
`,Rc=g.A.div`
  font-size: ${e=>e.theme.ketcher.font.size.regular};
  line-height: ${e=>e.theme.ketcher.font.size.regular};
  font-weight: 600;
  padding-right: 2px;
`,Oc=g.A.div`
  color: ${e=>e.theme.ketcher.color.text.lightgrey};
  font-size: ${e=>e.theme.ketcher.font.size.regular};
  font-weight: 400;
  white-space: nowrap;
`,zc=g.A.p`
  color: ${e=>e.theme.ketcher.color.text.primary};
  font-size: ${e=>e.theme.ketcher.font.size.regular};
  font-weight: 700;
  word-break: break-all;
  text-align: center;
  margin-top: 0;
  margin-bottom: 8px;
`,$c=(0,g.A)(f.In)`
  height: 14px;
  width: 14px;
  margin-right: 4px;
  color: ${({theme:e})=>e.ketcher.color.icon.grey};
  stroke: ${({theme:e})=>e.ketcher.color.icon.grey};
`,jc=(0,g.A)(f.In)`
  height: 15px;
  width: 15px;
  flex-shrink: 0;
`,_c=g.A.span`
  display: inline-flex;
  align-items: center;
  margin-left: 6px;
`,Hc=e=>{switch(e.props.MonomerClass){case c.IPc.Sugar:return"sugar";case c.IPc.Base:return"base";case c.IPc.Phosphate:return"phosphate";default:return"chem"}},qc=(0,g.A)(({className:e})=>{const t=Dr(At),{monomers:o,name:r,position:a,idtAliases:s,aliasAxoLabs:l}=t,[,d]=o,c=r??d?.props.Name,p=l??d?.props.aliasAxoLabs,u=(({presetName:e,position:t,idtAliases:o})=>(0,i.useMemo)(()=>{if(!e||!o)return null;if(e.includes("MOE")){const{base:e,modifications:n}=o,r=Yr(n?.endpoint5)??`5${e}`,i=Yr(n?.internal)??`i${e}`,a=Yr(n?.endpoint3)??`3${e}`;switch(t){case We.Library:return[r,i,a].every(t=>t.includes(e))?e:`${r}, ${i}`;case We.ChainStart:return r;case We.ChainMiddle:return i;case We.ChainEnd:return a}}return Yr(o.base)},[e,t,o]))({presetName:c,position:a,idtAliases:s}),h=u||p;let m,g;m="left"===t.phosphatePosition?"preset-left-phosphate":"right"===t.phosphatePosition?"preset-right-phosphate":void 0,g="left"===t.phosphatePosition?"Phosphate on the left (5')":"right"===t.phosphatePosition?"Phosphate on the right (3')":void 0;const f=e=>`(${e.props.Name})`;return(0,n.FD)(Bc,{className:e,style:{alignItems:"flex-start"},"data-testid":"polymer-library-preview","data-idtaliases":u??void 0,"data-axolabs":p??void 0,children:[(0,n.Y)(zc,{"data-testid":"preview-tooltip-title",children:c}),o.map((e,o)=>e?(0,n.FD)(Tc,{children:[(0,n.Y)($c,{name:Hc(e)}),(0,n.Y)(Rc,{children:e.label}),(0,n.Y)(Oc,{children:f(e)}),2===o&&m&&(0,n.Y)(_c,{title:g,"data-testid":"preset-preview-phosphate-position-icon","data-phosphate-position":t.phosphatePosition,children:(0,n.Y)(jc,{name:m})})]},e.props.id??o):null),h&&(0,n.Y)(Yc,{preset:!0,idtAliasesText:u??void 0,axoLabsText:p})]})})``,Vc=g.A.div`
  display: flex;
  flex-direction: column;
  align-items: start;
  gap: 8px;
  background: ${e=>e.theme.ketcher.color.background.primary};
  border: ${e=>e.theme.ketcher.border.regular};
  border-radius: ${e=>e.theme.ketcher.border.radius.regular};
  box-shadow: ${e=>e.theme.ketcher.shadow.regular};
`,Wc=g.A.div(({connected:e,inBond:t,theme:o})=>({display:"flex",flexDirection:"column",alignItems:"center",gap:"2px",color:t||!e?o.ketcher.color.text.primary:"#B4B9D6"})),Uc=g.A.p(({theme:e})=>({margin:0,fontSize:e.ketcher.font.size.small,lineHeight:"12px"})),Kc=g.A.p(({inBond:e,theme:t})=>({margin:0,padding:"4px",fontWeight:t.ketcher.font.weight.bold,lineHeight:"14px",borderRadius:"4px",backgroundColor:e?"#CDF1FC":"transparent",minWidth:e?"8px":void 0,minHeight:e?"14px":void 0})),Gc=({attachmentPoints:e,attachmentPointInBond:t})=>(0,n.Y)(n.FK,{children:e.map(e=>{const o=e.id===t;return(0,n.FD)(Wc,{connected:e.connected,inBond:o,children:[(0,n.Y)(Uc,{children:e.id}),(e.label||o)&&(0,n.Y)(Kc,{inBond:o,children:e.label})]},e.id)})}),Qc=(0,g.A)(({className:e})=>{const t=Dr(At),{polymerBond:o,style:r}=t,{firstMonomer:i,secondMonomer:a,firstMonomerAttachmentPoint:s,secondMonomerAttachmentPoint:l}=o,{preparedAttachmentPointsData:d,connectedAttachmentPoints:p}=Ac({monomerCaps:i.monomerCaps,attachmentPointsToBonds:i.attachmentPointsToBonds}),{preparedAttachmentPointsData:u,connectedAttachmentPoints:h}=Ac({monomerCaps:a?.monomerCaps,attachmentPointsToBonds:a?.attachmentPointsToBonds});return i&&a?(0,n.Y)(Vc,{className:e,"data-testid":"polymer-library-preview",style:{top:r?.top,left:r?.left,right:r?.right},children:(0,n.Y)(Wl,{firstMonomer:i,secondMonomer:a,firstMonomerOverview:(0,n.Y)(jl,{monomer:i,usage:c.HT5.BondPreview,connectedAttachmentPoints:p,selectedAttachmentPoint:s,attachmentPoints:(0,n.Y)(Gc,{attachmentPoints:d,attachmentPointInBond:s})}),secondMonomerOverview:(0,n.Y)(jl,{monomer:a,usage:c.HT5.BondPreview,connectedAttachmentPoints:h,selectedAttachmentPoint:l,attachmentPoints:(0,n.Y)(Gc,{attachmentPoints:u,attachmentPointInBond:l})})})}):null})`
  width: ${f.VH.widthForBond}px;
  height: ${f.VH.heightForBond}px;
`,Zc=g.A.div`
  padding: 4px 8px;
  background: ${e=>e.theme.ketcher.color.background.primary};
  border: ${e=>e.theme.ketcher.border.regular};
  border-radius: ${e=>e.theme.ketcher.border.radius.regular};
  box-shadow: ${e=>e.theme.ketcher.shadow.regular};
  font-size: 12px;
  white-space: nowrap;
`,Xc=()=>{const e=Dr(At);return(0,n.Y)(Zc,{"data-testid":"text-preview",children:e.text})},Jc=g.A.div`
  display: inline-block;
  position: absolute;
  background: ${({theme:e})=>e.ketcher.color.background.primary};
  z-index: ${({theme:e})=>e.ketcher.zIndex.overlay};
`,ep=()=>{const e=Dr(At),t=(0,i.useRef)(null),o=Boolean(e?.type),a=(0,r.d4)(kt);return(0,i.useLayoutEffect)(()=>{if(t.current&&!e.style)if(e?.type){t.current.setAttribute("style","");const o=5,n=t.current?.getBoundingClientRect(),r=n?.height||0,i=n?.width||0,s=c.OQ2.instance?.canvasWrapper.node()?.getBoundingClientRect(),l=s?.top||0,d=s?.bottom||0,p=s?.left||0,u=s?.right||0,h=e.target?.getBoundingClientRect(),m=h?.top||0,g=h?.bottom||0,f=h?.left||0,b=h?.width||0,x=f-b/2,y=a?.ketcherRootElementBoundingClientRect,v=y?.x||0,w=y?.y||0,A=m-r-o-w,C=g+o-w,M=f+b/2-i/2-v;if(t.current.style.top=m-r-o>=l||g+r>d&&g>d/2?`${A}px`:`${C}px`,x>i/2&&x+i/2<u)t.current.style.left=`${M}px`;else if(x<i/2)t.current.style.left=`${p}px`;else{const e=10;t.current.style.left=u-i-e+"px"}}else t.current.setAttribute("style","")},[a?.ketcherRootElementBoundingClientRect,o,e]),e?(0,n.FD)(Jc,{ref:t,style:{...e?.style},children:[e.type===Ve.Monomer&&(0,n.Y)(Pc,{}),e.type===Ve.Preset&&(0,n.Y)(qc,{}),e.type===Ve.Bond&&(0,n.Y)(Qc,{}),e.type===Ve.AmbiguousMonomer&&(0,n.Y)(f.ye,{preview:e}),e.type===Ve.Text&&(0,n.Y)(Xc,{})]}):null},tp=()=>{let e;try{e=ro.getItem(Jt)}catch(o){return c.$yr.error("sequenceTypeStorage.ts::getPersistedSequenceType",o),c.Qjs.RNA}return(t=e)===c.Qjs.RNA||t===c.Qjs.DNA||t===c.Qjs.PEPTIDE?e:c.Qjs.RNA;var t},op=(0,c.MxE)(c.KtH),np=(e,t)=>{t&&(t.value=`${Math.round(e)}%`)},rp=(0,g.A)(P.A)(({theme:e,variant:t})=>({color:"outlined"===t?e.ketcher.color.text.primary:e.ketcher.color.button.text.primary,boxShadow:"none",transition:"none",display:"flex",justifyContent:"center",alignItems:"center",cursor:"pointer",padding:"4px 8px",border:"outlined"===t?e.ketcher.outline.small:"none",background:"outlined"===t?e.ketcher.color.background.primary:e.ketcher.color.button.group.active,borderRadius:e.ketcher.border.radius.regular,textTransform:"none",fontSize:e.ketcher.font.size.regular,fontWeight:e.ketcher.font.weight.regular,":hover":{color:"outlined"===t?e.ketcher.color.text.dark:e.ketcher.color.button.text.primary,background:"outlined"===t?e.ketcher.color.background.primary:e.ketcher.color.button.group.hover,boxShadow:"none"},":disabled":{cursor:"auto",background:"#e1e5ea",outline:"none"}})),ip=()=>{const e=Dr(kt),[t,o]=(0,i.useState)(e?.events.changeSequenceTypeEnterMode),r=Dr(It),a="sequence-layout-mode"===Er(),s=!!r,l=Lr();(0,i.useEffect)(()=>{const t=e=>{var t;l(bo(e===qt?oo:no)),o(e),t=e,ro.setItem(Jt,t)};return e?.events.changeSequenceTypeEnterMode.add(t),e?.events.changeSequenceTypeEnterMode.dispatch(tp()),()=>{e?.events.changeSequenceTypeEnterMode.remove(t)}},[e,l]);const d=t=>{e?.events.changeSequenceTypeEnterMode.dispatch(t)};return a?(0,n.Y)(x.A,{sx:{mr:1,ml:1},children:(0,n.FD)(R.A,{disabled:s,children:[(0,n.Y)(rp,{"data-testid":`${c.Qjs.RNA}Btn`,title:`RNA (${op.RNASequenceType})`,variant:t===c.Qjs.RNA?"contained":"outlined",onClick:()=>d(c.Qjs.RNA),children:"RNA"}),(0,n.Y)(rp,{"data-testid":`${c.Qjs.DNA}Btn`,title:`DNA (${op.DNASequenceType})`,variant:t===c.Qjs.DNA?"contained":"outlined",onClick:()=>d(c.Qjs.DNA),children:"DNA"}),(0,n.Y)(rp,{"data-testid":`${c.Qjs.PEPTIDE}Btn`,title:`Peptides (${op.PEPTIDESequenceTYpe})`,variant:t===c.Qjs.PEPTIDE?"contained":"outlined",onClick:()=>d(c.Qjs.PEPTIDE),children:"PEP"})]})}):null},ap=()=>{const e=Lr(),t=Dr(kt),o=Dr(Bt),n=Dr(Tt),r=Dr(Rt),a=Dr(Ot);return(0,i.useCallback)(async i=>{if(!t||i)return;const s=f.e3.getIndigo(),l=t.drawingEntitiesManager.filterSelection(),d=new c.BBJ,p=!l.hasDrawingEntities,u=l.hasDrawingEntities?l:t.drawingEntitiesManager,h=c.NbU.fromMonomers([...u.monomers.values()]),m=h.firstNode?.monomer,g=new Set(u.monomers.values()),b=!m||h.chains.reduce((e,t)=>e+t.monomers.length,0)<=(0,c.eXT)(m,g).length,x=0===h.chains.length&&[...u.monomers.values()].filter(e=>e.monomerItem.props.isMicromoleculeFragment).length>1;if(!u.hasDrawingEntities||!b||p&&x)return void e(gt(void 0));const y=d.serialize(new c._kM,t.drawingEntitiesManager,void 0,!1,!0),v=await s.calculateMacromoleculeProperties({struct:y},{upc:r/Xe[o],nac:a/Xe[n]});try{const t=v.properties&&JSON.parse(v.properties);(0,c.qHi)(),e(gt(t))}catch(w){c.$yr.error("Error during parsing macromolecule properties: ",w),e(gt(void 0))}},[e,t,n,a,o,r])},sp="Other",lp="-",dp=(e,t)=>{const o=e?.monomerCount?.[t];return o&&Object.keys(o).length>0},cp=(0,g.A)("div")(({hasError:e})=>({width:"100%",height:e?"124px":"177px",position:"relative",backgroundColor:"white",padding:"2px 0",borderRadius:"8px 8px",overflow:"hidden",boxShadow:"0px 2px 5px rgba(103, 104, 132, 0.15)"})),pp=(0,g.A)("div")(()=>({display:"flex",justifyContent:"flex-end"})),up=(0,g.A)(f.In)(()=>({display:"flex",alignItems:"center",width:"12px",height:"12px",padding:"2px 6px",cursor:"pointer"})),hp=(0,g.A)("div")(()=>({display:"flex",height:"25px",alignItems:"center",padding:"0 8px"})),mp=(0,g.A)("div")(()=>({display:"flex",alignItems:"baseline",fontSize:"14px",fontWeight:"700",padding:"0 8px",color:"#585858"})),gp=(0,g.A)("sub")({verticalAlign:"baseline",fontSize:"0.75em",position:"relative",top:"0.3em"}),fp=(0,g.A)("div")(()=>({display:"flex",alignItems:"center",height:"24px",borderLeft:"1px solid #CAD3DD",color:"#585858"})),bp=(0,g.A)("div")(()=>({fontSize:"14px",fontWeight:"700",padding:"0 8px"})),xp=(0,g.A)("div")(()=>({width:"100%",height:"100%",top:"-25px",position:"relative"})),yp=(0,g.A)("div")(()=>({width:"100%",padding:"0 4px 4px"})),vp=(0,g.A)("div")(()=>({display:"flex",width:"100%",height:"74px",flexDirection:"column",justifyContent:"center",alignItems:"center"})),wp=(0,g.A)("div")(()=>({fontSize:"14px"})),Ap=(0,g.A)("div")(()=>({fontSize:"12px"})),Cp=(0,g.A)("div")(()=>({display:"flex",padding:"4px 0px",height:"32px",gap:"12px"})),Mp=(0,g.A)("div")(()=>({display:"grid",gridTemplateColumns:"2fr 1fr",gap:"0 2px"})),kp=(0,g.A)("div")(()=>({height:"90px",backgroundColor:"white",borderRadius:"8px",padding:"5px"})),Ip=(0,g.A)("div")(()=>({display:"flex",justifyContent:"space-between"})),Sp=(0,g.A)("div")(()=>({display:"grid",gridTemplateColumns:"2fr 1fr"})),Yp=(0,g.A)("div")(({disabled:e})=>({display:"flex",alignItems:"center",padding:"0 0 0 6px",pointerEvents:e?"none":"auto",opacity:e?.5:1})),Lp=(0,g.A)(({className:e,...t})=>(0,n.Y)(j.A,{...t,classes:{popper:e}}))(()=>({[`& .${_.A.tooltip}`]:{maxWidth:"220px",padding:"12px",backgroundColor:"white",color:"rgba(0, 0, 0, 0.87)",boxShadow:"0 1px 5px 0 #CCCCCC",fontSize:11}})),Dp=(0,g.A)("div")(()=>({display:"flex",flexDirection:"column",gap:"4px",fontSize:"12px",fontWeight:"700",color:"#585858",borderBottom:"1px solid #585858",paddingBottom:"8px",marginBottom:"8px"})),Ep=(0,g.A)("div")(()=>({fontSize:"10px",paddingRight:"5px",whiteSpace:"nowrap"})),Fp=(0,g.A)("div")(()=>({fontSize:"14px",fontWeight:"700"})),Np=(0,g.A)(f.In)(()=>({width:"20px",height:"20px"})),Pp=(0,g.A)("div")(()=>({display:"flex",alignItems:"center"})),Bp=(0,g.A)(zs)(()=>({position:"relative",padding:"0 0 0 5px",zIndex:1})),Tp="text-input-field-input",Rp=(0,g.A)(yl)(()=>({margin:0,[`.${Tp}`]:{width:"60px","::-webkit-inner-spin-button":{WebkitAppearance:"none",margin:0},"::-webkit-outer-spin-button":{WebkitAppearance:"none",margin:0},MozAppearance:"textfield"}})),Op=(0,g.A)("div")(()=>({display:"grid",gridTemplateColumns:"repeat(8, 1fr)",gap:"4px 6px",width:"100%",backgroundColor:"white",borderRadius:"8px",padding:"6px",height:"90px",alignContent:"flex-start"})),zp=(0,g.A)("div")`
  display: flex;
  position: relative;
  justify-content: space-between;
  background-color: #eff2f5;
  padding: 4px 6px;
  font-weight: 500;
  font-size: 12px;
  border-radius: 2px;
  min-width: 50px;
  flex: 1;
  opacity: ${({disabled:e})=>e?.4:1};

  &:after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    width: 15px;
    height: 2px;
    background: ${({theme:e,monomerShortName:t,isPeptide:o})=>{const n=o?e.ketcher.peptide.color:e.ketcher.monomer.color;return n[t]?.regular||e.ketcher.monomer.color.default.regular}};
    border-radius: 0 0 0 2px;
  }
`,$p=(0,g.A)("div")(()=>({fontWeight:"700"})),jp=e=>{const t=(e.isPeptide?c.Q_X:c.b$S).map(t=>[t,e.monomerCount?.[t]]);return t.push([sp,e.monomerCount?.[sp]]),t.sort((e,t)=>e[0]===sp?1:e[0].localeCompare(t[0])),(0,n.Y)(Op,{children:X()(t,([t,o])=>(0,n.FD)(zp,{monomerShortName:t,"data-testid":t+"-option",isPeptide:e.isPeptide,disabled:0===o,children:[(0,n.Y)($p,{children:t}),(0,n.Y)("div",{children:void 0===o?lp:o})]},t))})},_p=e=>(0,n.Y)(Yp,{"data-testid":e.testId,disabled:e.disabled,children:(0,n.FD)(n.FK,{children:[(0,n.FD)(Ep,{children:[e.name,void 0!==e.value&&":"]}),e.onChangeValue?(0,n.Y)(Rp,{value:e.value??"",id:`macromolecule-property-${e.name}`,"data-testid":`${e.testId}-input`,type:"number",min:0,inputClassName:Tp,onChange:t=>e?.onChangeValue?.(Number(t))}):(0,n.Y)(Fp,{"data-testid":e.testId+"-value",children:e.value}),e.hint&&(0,n.Y)(Lp,{title:e.hint,children:(0,n.Y)(Pp,{children:(0,n.Y)(Np,{name:"about",dataTestId:e.name+"-info"})})}),e.options&&e.selectedOption&&e.onChangeOption&&(0,n.Y)(Bp,{options:e.options.map(e=>({id:e,label:e})),testId:e.testId+"-selector",currentSelection:e.selectedOption,selectionHandler:e.onChangeOption})]})}),Hp=({part:e})=>{const t=e.match(/^([A-Za-z]+)(\d+)$/);if(!t)return e;const[o,r,i]=t;return(0,n.FD)("span",{children:[r,(0,n.Y)(gp,{children:i})]})},qp=(0,g.A)("svg")(()=>({width:"100%",height:"100%"}));const Vp=e=>e<1?0:e<2?1:e<3?2:e<5?3:e<10?5:e<100?10*Math.floor(e/10):e<1e3?50*Math.floor(e/50):100*Math.floor(e/100),Wp=e=>{const{data:t}=e,o=function(e,t,o){const n=e.length;if(o>=n||0===o)return{xs:e,ys:t};const r=[e[0]],i=[t[0]],a=(n-2)/(o-2);let s=0;for(let l=0;l<o-2;l++){const o=Math.floor((l+1)*a)+1,n=Math.floor((l+2)*a)+1;let d=0,c=0;const p=n-o;for(let r=o;r<n;r++)d+=e[r],c+=t[r];d/=p,c/=p;const u=Math.floor(l*a)+1,h=Math.floor((l+1)*a)+1;let m=-1,g=u;for(let r=u;r<h;r++){const o=Math.abs((e[s]-d)*(t[r]-t[s])-(e[s]-e[r])*(c-t[s]));o>m&&(m=o,g=r)}r.push(e[g]),i.push(t[g]),s=g}return r.push(e[n-1]),i.push(t[n-1]),{xs:r,ys:i}}(t.map((e,t)=>t+1),t,100),[r,a]=(0,i.useState)(0),s=(0,i.useRef)(null);(0,i.useEffect)(()=>{if(!s.current)return;const e=s.current.width.baseVal.value,n=s.current.height.baseVal.value,r=10,i=10,a=20,l=30;$.Ltv(s.current).selectAll("*").remove();const d=$.m4Y().domain([0,Math.max(o.xs.length-1,1)]).range([l,e-i]),c=$.m4Y().domain([0,1]).range([n-a,r]),p=$.n8j().x((e,t)=>d(t)).y(e=>c(e)).curve($.lUB),u=$.Ltv(s.current),h=(m=e)>360?5:m>300?4:m>240?3:2;var m;const g=Vp(t.length/h);let f,b=g;if(g>=1){let e=-1/0,n=-1/0,r=-1/0;for(let o=2;o<=h;o++){const i=o,a=Vp(t.length/i),s=Number((a*(i/t.length)).toFixed(2));(s>r||s===r&&i>e)&&(e=i,r=s,n=a)}f=Array.from({length:e},(e,t)=>o.xs.findLastIndex(e=>e<=(t+1)*n&&e>t*n)),b=n}else f=[...Array(Math.min(h,o.xs.length)).keys()],b=1;if(o.xs.length){const e=$.l78(d).tickValues(f).tickFormat((e,t)=>((t+1)*b).toString());u.append("g").attr("transform",`translate(0,${n-a})`).call(e).call(e=>e.select(".domain").remove()).call(e=>e.selectAll("line").attr("stroke","#CAD3DD").attr("y1",-n)).call(e=>e.selectAll("text").attr("font-size","8px"))}const x=$.V4s(c).tickValues([0,.5,1]).tickFormat($.GPZ(".1f"));u.append("g").attr("transform",`translate(${l},0)`).call(x).call(e=>e.select(".domain").remove()).call(t=>{t.selectAll("line").each((t,o,n)=>{.5===t?$.Ltv(n[o]).attr("x1",e).attr("stroke","#CAD3DD").attr("stroke-dasharray",2):$.Ltv(n[o]).remove()})}).call(e=>e.selectAll("text").attr("font-size","8px")),o.ys.length&&u.append("path").datum(o.ys).attr("fill","none").attr("stroke","#167782").attr("stroke-width",1).attr("d",p)},[t,r]);const l=new ResizeObserver((0,d.debounce)(e=>{for(const t of e)t.contentRect.width!==r&&a(t.contentRect.width)},100));return(0,i.useEffect)(()=>(s.current&&l.observe(s.current),()=>{l.disconnect()}),[s,r]),(0,n.Y)(qp,{"data-testid":"Hydrophobicity-Chart",ref:s})},Up=e=>e.isError?(0,n.FD)(vp,{children:[(0,n.Y)(wp,{children:"No Data Available"}),(0,n.Y)(Ap,{children:"Select monomer, chain or part of a chain"})]}):(0,n.FD)(yp,{children:[(0,n.FD)(Sp,{children:[(0,n.FD)(Cp,{children:[(0,n.Y)(_p,{name:"Isoelectric Point",testId:"Isoelectric Point",value:(0,d.isNumber)(e.macromoleculesProperties.pKa)?Q()(e.macromoleculesProperties.pKa,2):lp,hint:(0,n.FD)("div",{children:["The isoelectric point is calculated as the median of all pKa values for amino acids (values from"," ",(0,n.Y)("i",{children:"Miclotte et. al. (2020))"}),". Only amino acid natural analogues are used in the calculation."]})}),(0,n.Y)(_p,{name:"Extinction Coef.(1/Mcm)",testId:"Extinction Coefficient",value:(0,d.isNumber)(e.macromoleculesProperties.extinctionCoefficient)?Q()(e.macromoleculesProperties.extinctionCoefficient):lp,hint:(0,n.FD)("div",{children:["The extinction coefficient for wavelength of 280nm is calculated using the method from"," ",(0,n.Y)("i",{children:"Gill, S.C. and von Hippel, P.H. (1989)."})," Natural analogue is used in place of a modified amino acid."]})})]}),(0,n.Y)(_p,{name:"Hydrophobicity",testId:"Hydrophobicity",hint:(0,n.FD)("div",{children:[(0,n.FD)(Dp,{children:[(0,n.Y)("div",{children:"y = Hydrophobicity score"}),(0,n.Y)("div",{children:"x = Position of the amino acid residue"})]}),"The hydrophobicity is calculated using the method from"," ",(0,n.Y)("i",{children:"Black S.D. and Mould D.R. (1991)."})," Natural analogue is used in place of a modified amino acid."]})})]}),(0,n.FD)(Mp,{children:[(0,n.Y)(jp,{monomerCount:e.macromoleculesProperties.monomerCount.peptides,isPeptide:!0}),(0,n.Y)(kp,{children:e.macromoleculesProperties.hydrophobicity&&(0,n.Y)(Wp,{data:e.macromoleculesProperties.hydrophobicity})})]})]}),Kp=e=>!/[^0.,]/.test(String(e)),Gp=e=>{const t=Lr(),o=Dr(Bt),r=Dr(Tt),i=Dr(Rt),a=Dr(Ot);return e.isError?(0,n.FD)(vp,{children:[(0,n.Y)(wp,{children:"No Data Available"}),(0,n.Y)(Ap,{children:"Select a nucleotide/nucleoside, chain or part of a chain containing nucleotides/nucleosides"})]}):(0,n.FD)(yp,{children:[(0,n.FD)(Ip,{children:[(0,n.Y)(_p,{name:"Melting Temp. (\xb0C)",value:(0,d.isNumber)(e.macromoleculesProperties.Tm)?Q()(e.macromoleculesProperties.Tm,1):lp,testId:"Melting-Temperature",hint:(0,n.FD)("div",{children:["The melting temperature is calculated using the method from"," ",(0,n.Y)("i",{children:"Khandelwal G. and Bhyravabhotla J. (2010)."})," Natural analogue is used in place of a modified base."]})}),(0,n.FD)(Cp,{children:[(0,n.Y)(_p,{name:"[Unipositive Ions]",value:i,options:["nM","\u03bcM","mM"],testId:"Unipositive Ions",selectedOption:o,disabled:!(0,d.isNumber)(e.macromoleculesProperties.Tm)&&!Kp(i),onChangeOption:e=>{t(ft(e))},onChangeValue:e=>{t(yt(e.toString()))}}),(0,n.Y)(_p,{name:"[Oligonucleotides]",value:a,options:["nM","\u03bcM","mM"],testId:"Oligonucleotides",selectedOption:r,disabled:!(0,d.isNumber)(e.macromoleculesProperties.Tm)&&!Kp(a),onChangeOption:e=>{t(bt(e))},onChangeValue:e=>{t(vt(e.toString()))}})]})]}),(0,n.Y)(jp,{monomerCount:e.macromoleculesProperties.monomerCount.nucleotides})]})};var Qp,Zp;!function(e){e[e.PEPTIDES=0]="PEPTIDES",e[e.RNA=1]="RNA",e[e.NO_TAB=-1]="NO_TAB"}(Qp||(Qp={})),function(e){e.Da="Da",e.kDa="kDa",e.MDa="MDa"}(Zp||(Zp={}));const Xp={[Zp.Da]:1,[Zp.kDa]:1e3,[Zp.MDa]:1e6},Jp=e=>(0,d.isNumber)(e)?e<1e3?Zp.Da:e<1e6?Zp.kDa:Zp.MDa:Zp.Da,eu=e=>dp(e,"nucleotides")?Qp.RNA:Qp.PEPTIDES;let tu;const ou=()=>{const e=Lr(),t=Dr(kt),o=Dr(Pt),r=Dr(Bt),a=Dr(Tt),s=Dr(Rt),l=Dr(Ot),c=o?.[0],[p,u]=(0,i.useState)(()=>eu(c)),[h,m]=(0,i.useState)(()=>Jp(c?.mass)),g=Dr(Nt),f=ap(),b=!g,{debouncedCallback:x,invokeImmediately:y,cancel:v}=Pr(f,500);(0,i.useEffect)(()=>(tu&&(t?.events.selectEntities.hasHandler(tu)&&t?.events.selectEntities.remove(tu),t?.events.modelChange.hasHandler(tu)&&t?.events.modelChange.remove(tu)),tu=()=>{x(b)},t?.events.selectEntities.add(tu),t?.events.modelChange.add(tu),()=>{t?.events.selectEntities.remove(tu),t?.events.modelChange.remove(tu)}),[x,t,b]),(0,i.useEffect)(()=>{x(b)},[s,l,b,x]),(0,i.useEffect)(()=>{v(),y(b)},[r,a,b,v,y]);const[w,A]=(0,i.useState)(c);w!==c&&(A(c),u(eu(c)),m(Jp(c?.mass)));const C=!c||o.length>1,M=C||!dp(c,"peptides"),k=C||!dp(c,"nucleotides"),I=(0,i.useMemo)(()=>(0,n.Y)(mp,{"data-testid":"Gross-formula",children:(c?.grossFormula||lp).split(" ").map((e,t,o)=>(0,n.FD)("span",{children:[(0,n.Y)(Hp,{part:e}),t<o.length-1?" ":""]},`${e}-${t}`))}),[c?.grossFormula]),S=(0,i.useMemo)(()=>(0,d.isNumber)(c?.mass)?(0,n.FD)(n.FK,{children:[(0,n.Y)(bp,{"data-testid":"Molecular-Mass-Value",children:Q()(c?.mass/Xp[h],3)})," "]}):(0,n.Y)(bp,{"data-testid":"Molecular-Mass-Value",children:lp}),[c?.mass,h]);return g?(0,n.FD)(cp,{hasError:p===Qp.PEPTIDES&&M||p===Qp.RNA&&k,"data-testid":"macromolecule-properties-window",children:[(0,n.Y)(pp,{children:(0,n.Y)(up,{name:"close",onClick:()=>{e(ht(!1))},dataTestId:"macromolecule-properties-close"})}),(0,n.FD)(hp,{children:[I,S&&(0,n.FD)(fp,{children:[S,(0,d.isNumber)(c?.mass)&&(0,n.Y)(Bp,{testId:"Molecular Mass Unit",options:[Zp.Da,Zp.kDa,Zp.MDa].map(e=>({id:e,label:e})),currentSelection:h,selectionHandler:e=>{m(e)}})]})]}),(0,n.Y)(xp,{children:(0,n.Y)(Mr,{selectedTabIndex:p,onChange:(e,t)=>{u(t)},isLayoutToRight:!0,tabs:[{caption:"Peptides",testId:"peptides-properties-tab",component:Up,props:{macromoleculesProperties:c,isError:M}},{caption:"RNA/DNA",component:Gp,testId:"rna-properties-tab",props:{macromoleculesProperties:c,isError:k}}]})})]}):null},nu=(0,g.A)(f.$n)(({theme:e,isActive:t})=>({width:"28px",height:"28px",backgroundColor:t?e.ketcher.color.button.group.active:"white",margin:"2px",padding:"0",borderRadius:"4px",outline:"none",":hover":{backgroundColor:t?e.ketcher.color.button.group.hover:"white"},":hover svg":{fill:t?"white":e.ketcher.color.button.group.active}})),ru=()=>{const e=Lr(),t=Dr(Nt),o=ap();return(0,n.Y)(nu,{isActive:t,onClick:async()=>{const n=!t;await o(n),e(mt({})),Hd()},title:`Calculate properties (${op.toggleMacromoleculesPropertiesVisibility})`,"data-testid":"calculate-macromolecule-properties-button",children:(0,n.Y)("svg",{width:"15",height:"16",viewBox:"0 0 15 16",xmlns:"http://www.w3.org/2000/svg",fill:t?"white":"#333333",children:(0,n.Y)("path",{fillRule:"evenodd",clipRule:"evenodd",d:"M9.87727 1.65517H9.03053V4.46737L9.42288 5.12973C9.76799 5.04496 10.1287 5 10.5 5C12.9853 5 15 7.01472 15 9.5C15 10.6812 14.5449 11.7562 13.8004 12.559C14.4936 14.1572 13.3016 16 11.456 16H2.54694C0.627617 16 -0.60048 14.0008 0.302223 12.3451C1.6087 9.94883 3.5345 6.39671 4.51457 4.49368V1.65517H3.66783V0H9.87727V1.65517ZM12.2962 13.6272C11.746 13.867 11.1385 14 10.5 14C8.01472 14 6 11.9853 6 9.5C6 7.98987 6.74386 6.65348 7.88515 5.83725L7.33705 4.91194V1.65517H6.20806V4.88502L6.11866 5.05976C5.15873 6.93619 3.14099 10.658 1.79676 13.1235C1.49524 13.6765 1.9056 14.3448 2.54694 14.3448H11.456C11.9075 14.3448 12.2417 14.0139 12.2962 13.6272ZM13.5375 9.5C13.5375 11.1776 12.1776 12.5375 10.5 12.5375C8.82244 12.5375 7.4625 11.1776 7.4625 9.5C7.4625 7.82243 8.82244 6.4625 10.5 6.4625C12.1776 6.4625 13.5375 7.82243 13.5375 9.5Z"})})})};function iu(){const e=Lr(),t=Dr(Ct),o=Dr(kt),r=Er(),a=Dr(It),[s,l]=(0,i.useState)([]),[d,p]=(0,i.useState)(!1),[u,h]=(0,i.useState)(),m=[t],g=a,f=Dr(jt),b="flex-layout-mode"===r,x=s.filter(e=>e&&"function"===typeof e.forEachBond),y=(o?.drawingEntitiesManager.selectedMicromoleculeEntities.length??0)>0||!gc(x);(0,i.useEffect)(()=>{const e=e=>{l(e),e.length&&!dc(e)&&(p(!1),!pc(e)&&(e=>e?.every(e=>!(e instanceof c.HvV)||e.label===c.Cuv.SUGAR_DNA))(e)?h("antisenseDnaStrand"):!pc(e)&&(e=>e?.every(e=>!(e instanceof c.HvV)||e.label===c.Cuv.SUGAR_RNA))(e)?h("antisenseRnaStrand"):(h("antisenseStrand"),p(!0)))};return o?.events.selectEntities.add(e),()=>{o?.events.selectEntities.remove(e)}},[o]);return(0,n.FD)(oc,{onItemClick:t=>{gd[t]?e(Yo(t)):"undo"===t||"redo"===t?o?.events.selectHistory.dispatch(t):"clear"===t?(o?.events.resetSequenceEditMode.dispatch(),o?.events.selectTool.dispatch([t]),o?.events.selectTool.dispatch([f]),a&&Qi(e,o)):"antisenseRnaStrand"===t||"antisenseDnaStrand"===t?o?.events.createAntisenseChain.dispatch("antisenseDnaStrand"===t):"arrange-ring"===t&&o?.events.layoutCircular.dispatch()},activeMenuItems:m,isHorizontal:!0,children:[(0,n.FD)(oc.Group,{isHorizontal:!0,divider:!0,children:[(0,n.Y)(oc.Item,{itemId:"clear",title:`Clear Canvas (${op.clear})`,testId:"clear-canvas"}),(0,n.Y)(oc.Item,{itemId:"open",title:"Open...",disabled:g,testId:"open-file-button"}),(0,n.Y)(oc.Item,{itemId:"save",title:"Save as...",testId:"save-file-button"})]}),(0,n.FD)(oc.Group,{isHorizontal:!0,divider:!0,children:[(0,n.Y)(oc.Item,{itemId:"undo",title:`Undo (${op.undo})`,disabled:g,testId:"undo"}),(0,n.Y)(oc.Item,{itemId:"redo",title:`Redo (${op.redo})`,disabled:g,testId:"redo"})]}),b&&(0,n.Y)(oc.Group,{isHorizontal:!0,divider:!0,children:(0,n.Y)(oc.Item,{itemId:"arrange-ring",title:`Arrange as a Ring (${op.arrangeRing})`,disabled:y,testId:"arrange-ring"})}),(0,n.FD)(oc.Group,{isHorizontal:!0,children:[(0,n.FD)(oc.Submenu,{disabled:!s?.length||!cc(s)||dc(s),needOpenByMenuItemClick:d,vertical:!0,autoSize:!0,layoutModeButton:!0,generalTitle:"Create Antisense Strand",testId:"Create Antisense Strand",activeItem:u,children:[(0,n.Y)(oc.Item,{itemId:"antisenseRnaStrand",title:`Create RNA Antisense Strand (${op.createRnaAntisenseStrand})`,disabled:!s?.length||!cc(s)||dc(s),testId:"antisenseRnaStrand",type:"button"}),(0,n.Y)(oc.Item,{itemId:"antisenseDnaStrand",title:`Create DNA Antisense Strand (${op.createDnaAntisenseStrand})`,disabled:!s?.length||!cc(s)||dc(s),testId:"antisenseDnaStrand",type:"button"})]}),(0,n.Y)(ru,{})]})]})}function au(){const e=Dr(Ct),t=Dr(kt),o="sequence-layout-mode"===Er(),r=[e];return(0,n.FD)(oc,{testId:"left-toolbar",onItemClick:e=>{t?.events.selectTool.dispatch([e,{toolName:e}])},activeMenuItems:r,children:[(0,n.FD)(oc.Group,{divider:!0,children:[(0,n.Y)(oc.Item,{itemId:"hand",title:`Hand Tool (${op.hand})`,testId:"hand"}),(0,n.Y)(oc.Group,{children:(0,n.FD)(oc.Submenu,{testId:"select-drop-down-button",subMenuId:Ke,needOpenByMenuItemClick:!0,children:[(0,n.Y)(oc.Item,{itemId:"select-rectangle",title:`Select Rectangle (${op.switchSelectTool})`,testId:"select-rectangle"}),(0,n.Y)(oc.Item,{itemId:"select-lasso",title:`Lasso selection (${op.switchSelectTool})`,testId:"select-lasso"}),(0,n.Y)(oc.Item,{itemId:"select-structure",title:`Structure Selection (${op.switchSelectTool})`,testId:"select-structure"})]})}),(0,n.Y)(oc.Item,{itemId:"erase",title:`Erase (${op.erase})`,testId:"erase",disabled:o})]}),(0,n.Y)(oc.Group,{children:(0,n.FD)(oc.Submenu,{disabled:o,testId:"bonds-drop-down-button",needOpenByMenuItemClick:!1,children:[(0,n.Y)(oc.Item,{itemId:"bond-single",title:"Single Bond (1)",testId:"single-bond",disabled:o}),(0,n.Y)(oc.Item,{itemId:"bond-hydrogen",title:"Hydrogen Bond (2)",testId:"hydrogen-bond",disabled:o})]})})]})}const su=(0,g.A)("div")`
  position: relative;
`,lu=(0,g.A)(P.A)`
  display: flex;
  color: ${({theme:e})=>e.ketcher.color.dropdown.primary};
  padding-right: 0;
  padding-left: 0;

  & svg {
    margin-left: 2px;
    width: 1rem;
    height: 1rem;
  }
`,du=(0,g.A)("span")`
  width: 35px;
`,cu=(0,g.A)(O.Ay)`
  & .MuiPopover-paper {
    padding: 8px;
    width: 175px;
    border: none;
    border-radius: 0px 0px 4px 4px;
    box-shadow: ${({theme:e})=>e.ketcher.shadow.regular};
    box-sizing: border-box;
  }
`,pu=(0,g.A)("div")`
  display: flex;
  flex-direction: column;
  white-space: nowrap;
  word-break: keep-all;
  background: white;
`,uu=(0,g.A)(P.A)`
  display: flex;
  justify-content: space-between;
  font-size: ${({theme:e})=>e.ketcher.font.size.regular};
  line-height: 14px;
  padding: 7px 8px;
  text-transform: none;
  color: ${({theme:e})=>e.ketcher.color.dropdown.primary};
`,hu=(0,g.A)("span")`
  color: #cad3dd;
`,mu=(0,g.A)("input")`
  border: 1px solid #cad3dd;
  border-radius: 4px;
  padding: 3px 8px;
  color: ${({theme:e})=>e.ketcher.color.text.light};
  font-size: ${({theme:e})=>e.ketcher.font.size.medium};
  line-height: 16px;
  margin-bottom: 8px;

  &:hover {
    border-color: ${({theme:e})=>e.ketcher.color.input.border.hover};
  }

  &:active,
  &:focus {
    border-color: ${({theme:e})=>e.ketcher.outline.selected.color};
    outline: none;
  }

  &::after,
  &::before {
    display: none;
  }
`,gu=({onZoomSubmit:e,currentZoom:t,inputRef:o})=>{const r=(0,i.useCallback)(t=>{const n=o.current;if(!n)return;[op["zoom-out"],op["zoom-in"]].includes(t.key)||t.nativeEvent.stopImmediatePropagation(),"Enter"===t.key&&(e(),n.select())},[e,o]);return(0,i.useEffect)(()=>{const e=o.current;np(t,e),document.activeElement===e&&e?.select()},[t,o]),(0,i.useEffect)(()=>{const e=o.current;e?.focus(),e?.select()},[o]),(0,n.Y)(mu,{ref:o,"data-testid":"zoom-value",onFocus:e=>{e.target.select()},onKeyDown:r})},fu=()=>{const[e,t]=(0,i.useState)(!1),[o,r]=(0,i.useState)(100),[a,s]=(0,i.useState)(null),l=(0,i.useRef)(null),d=Dr(kt),p=d?.zoomTool;(0,i.useEffect)(()=>{if(!p)return;const e=()=>{r(Math.round(100*p.getZoomLevel()))};return p.subscribeOnZoomEvent(e),()=>{p.unsubscribeOnZoomEvent(e)}},[p]);const u=(0,i.useCallback)(()=>{const e=l.current;if(!e)return;const t=(e=>{const t=parseInt(e??"");return isNaN(t)?0:t})(e.value);if(t&&t!==o){const n=((e,t)=>{if(0===e)return t;const o=100*c.OQ2.instance.MINZOOMSCALE,n=100*c.OQ2.instance.MAXZOOMSCALE;return e<o?o:e>n?n:e})(t,o);np(n,e),c.OQ2.instance.zoomTo(n/100)}else np(o,e)},[o]);return(0,n.FD)(su,{ref:s,children:[(0,n.FD)(lu,{onClick:()=>{t(!0)},"data-testid":"zoom-selector",children:[(0,n.FD)(du,{"data-testid":"zoom-input",children:[o,"%"]}),(0,n.Y)(f.In,{name:"chevron"})]}),(0,n.Y)(cu,{open:e,onClose:()=>{t(!1)},anchorEl:a,container:document.querySelector(f.tS),anchorOrigin:{vertical:"bottom",horizontal:"right"},transformOrigin:{vertical:"top",horizontal:"right"},disableEnforceFocus:!0,children:(0,n.FD)(pu,{children:[(0,n.Y)(gu,{onZoomSubmit:u,inputRef:l,currentZoom:o}),(0,n.FD)(uu,{"data-testid":"zoom-out",title:"Zoom Out",onClick:()=>{c.OQ2.instance.zoomOut()},children:[(0,n.Y)("span",{children:"Zoom out"}),(0,n.Y)(hu,{children:op["zoom-minus"]})]}),(0,n.FD)(uu,{"data-testid":"zoom-in",title:"Zoom In",onClick:()=>{c.OQ2.instance.zoomIn()},children:[(0,n.Y)("span",{children:"Zoom in"}),(0,n.Y)(hu,{children:op["zoom-plus"]})]}),(0,n.FD)(uu,{"data-testid":"zoom-default",title:"Zoom 100%",onClick:()=>{c.OQ2.instance.resetZoom()},children:[(0,n.Y)("span",{children:"Zoom 100%"}),(0,n.Y)(hu,{children:op["zoom-reset"]})]})]})})]})};const bu=[c.w52.bondSingle,c.w52.selectRectangle],xu=()=>{const e=Dr(kt),t=Dr(Ct),o=Dr(Et),r=Lr(),a=Dr(xn),s=Dr(Dt),l=Dr(jt),d=(0,i.useCallback)(()=>{r(po(e?.monomersLibrary)),r(uo(e?.defaultRnaPresetsLibraryItems))},[e]);!function(){const e=Lr();(0,i.useEffect)(()=>{!async function(){const t=f.e3.getIndigo();if(t?.info)try{const o=await t.info();e(wt(o.indigoVersion??""))}catch(o){c.$yr.error("useIndigoVersionToRedux::fetchIndigoInfo - Failed to fetch Indigo version",o)}}()},[e])}(),(0,i.useEffect)(()=>(e?.events.updateMonomersLibrary.add(d),()=>{e?.events.updateMonomersLibrary.remove(d)}),[e,d]),(0,i.useEffect)(()=>{const t=()=>{e?.events.selectTool.dispatch([l])};return e&&e.events.selectSelectionTool.add(t),()=>{e?.events.selectSelectionTool.remove(t)}},[r,e,l]),(0,i.useEffect)(()=>{const t=([e])=>{r(at(e)),Qe(e)&&c.Vru.setSelectionTool("macro",{tool:"select",opts:Ge[e]})},o=e=>{r(Do(e))},n=e=>{r(Fo(e))},i=e=>r(Yo({name:"monomerConnection",additionalProps:e})),a=e=>r(Yo({name:"confirmationDialog",additionalProps:e}));if(e){e.events.error.add(o),e.events.openErrorModal.add(n),e.events.openMonomerConnectionModal.add(i),e.events.openConfirmationDialog.add(a),e.events.selectTool.add(t);const r=c.Vru.getSelectionTool("macro"),s=r?.opts?`select-${r.opts}`:"select-rectangle";e.events.selectTool.dispatch([s])}return()=>{r(at(null)),e?.events.selectTool.remove(t),e?.events.error.remove(o),e?.events.openErrorModal.remove(n),e?.events.openMonomerConnectionModal.remove(i),e?.events.openConfirmationDialog.remove(a)}},[e,r]);const{showPreview:p,closePreview:u}=Or(),h=(0,i.useCallback)((e,t)=>{const o={type:Ve.Bond,polymerBond:e,style:t};p(o)},[p]),m=(0,i.useCallback)(e=>{if(1===e.buttons)return;if(2===e.buttons)return;if(o)return e.preventDefault(),void e.stopPropagation();const t=e.target.__data__?.polymerBond;if(t&&!t.finished||t instanceof c.eOK)return;if(t){const o=(0,f.Xx)(t,e.target.getBoundingClientRect());return void h(t,o)}const n=e.target.__data__?.node,r=e.target.__data__?.monomer||n?.monomer;if(n&&n instanceof c.Dzo)return;if(r instanceof c.cN8){const t={type:Ve.AmbiguousMonomer,monomer:r.variantMonomerItem,target:e.target};return void p(t)}const i=r.monomerItem,s={...r.attachmentPointsToBonds},l=n instanceof c.NxW||n instanceof c.yYC;if(n instanceof c.dL6){const t=n.monomers;if(t.length>1){const o={type:Ve.Preset,monomers:t.map(e=>e.monomerItem),position:We.ChainMiddle,target:e.target};return void p(o)}}if(l){const t=n instanceof c.NxW?[n.sugar.monomerItem,n.rnaBase.monomerItem,n.phosphate?.monomerItem]:[n.sugar.monomerItem,n.rnaBase.monomerItem];if(n.rnaBase instanceof c.cN8){const o={type:Ve.AmbiguousMonomer,monomer:n.rnaBase.variantMonomerItem,presetMonomers:t,target:e.target};return void p(o)}const o=a.find(e=>{const o=[e.sugar,e.base,e.phosphate];return t.every((e,t)=>e?.props.Name===o[t]?.props.Name)});let r;r=n instanceof c.yYC?We.ChainEnd:void 0!==n.firstMonomerInNode.R1AttachmentPoint?We.ChainStart:We.ChainMiddle;const i={type:Ve.Preset,monomers:t,name:o?.name,idtAliases:o?.idtAliases,aliasAxoLabs:o?.aliasAxoLabs,phosphatePosition:n instanceof c.NxW?"right":void 0,position:r,target:e.target};return void p(i)}const d={type:Ve.Monomer,monomer:i,attachmentPointsToBonds:s,target:e.target};p(d)},[h,p,a,o]),g=(0,i.useCallback)(e=>{const t=e.target.__data__;if(!(t instanceof c.ZxT))return;const o=t?.labelTooltipText;if(!o)return;const n={type:Ve.Text,text:o,target:e.target};p(n)},[p]);return(0,i.useEffect)(()=>{e?.events.mouseOverMonomer.add(m),e?.events.mouseLeaveMonomer.add(u),e?.events.mouseLeaveAttachmentPoint.add(u),e?.events.mouseDownAttachmentPoint.add(u),e?.events.mouseOverSequenceItem.add(m),e?.events.mouseLeaveSequenceItem.add(u),e?.events.mouseOverPolymerBond.add(m),e?.events.mouseLeavePolymerBond.add(u),e?.events.mouseOverDrawingEntity.add(g),e?.events.mouseLeaveDrawingEntity.add(u);const o=e=>{u();1===e.buttons&&bu.includes(t)||m(e)};e?.events.mouseOnMoveMonomer.add(o),e?.events.mouseMoveAttachmentPoint.add(o),e?.events.mouseOnMoveSequenceItem.add(o),e?.events.mouseOnMovePolymerBond.add(o);const n=(0,c.VAU)(u);return window.addEventListener("hidePreview",n),()=>{e?.events.mouseOverMonomer.remove(m),e?.events.mouseLeaveMonomer.remove(u),e?.events.mouseLeaveAttachmentPoint.remove(u),e?.events.mouseDownAttachmentPoint.remove(u),e?.events.mouseOverSequenceItem.remove(m),e?.events.mouseLeaveSequenceItem.remove(u),e?.events.mouseOverPolymerBond.remove(m),e?.events.mouseLeavePolymerBond.remove(u),e?.events.mouseOverDrawingEntity.remove(g),e?.events.mouseLeaveDrawingEntity.remove(u),e?.events.mouseOnMoveMonomer.remove(o),e?.events.mouseMoveAttachmentPoint.remove(o),e?.events.mouseOnMoveSequenceItem.remove(o),e?.events.mouseOnMovePolymerBond.remove(o),window.removeEventListener("hidePreview",n)}},[e,t,m,u,g]),(0,i.useEffect)(()=>{s||e?.events.resetSequenceEditMode.dispatch()},[s,e]),(0,n.Y)(n.FK,{})},yu=({selectedMonomers:e,contextMenuEvent:t,isPasteAvailable:o=!0})=>{const r=e||[],i=Dr(kt),{hideAll:a}=(0,q.EF)({id:li.FOR_SELECTED_MONOMERS}),s=mc(r,t),l=e=>{const t=(i?.drawingEntitiesManager?.selectedEntitiesArr?.length??0)>0;return!e?.polymerBondRenderer&&(!e?.selectedMonomers||0===e?.selectedMonomers.length)&&!t},d=hc(s),c=e=>!!e?.polymerBondRenderer,p=r&&r.length>0&&cc(r),u="flex-layout-mode"===i?.mode.modeName,h=!u||i?.drawingEntitiesManager.selectedMicromoleculeEntities.length>0||!gc(r),m=[{name:"copy",title:"Copy",icon:(0,n.Y)(f.In,{name:"copyMenu"}),disabled:({props:e={}})=>c(e)||l(e)},{name:fc.paste,title:"Paste",icon:(0,n.Y)(f.In,{name:"pasteNavBar"}),disabled:({props:e={}})=>!l(e)||!o,separator:!0},{name:"create_antisense_rna_chain",title:"Create Antisense RNA Strand",separator:!1,disabled:dc(r),hidden:({props:e})=>!e?.selectedMonomers||!cc(e?.selectedMonomers)},{name:"create_antisense_dna_chain",title:"Create Antisense DNA Strand",disabled:dc(r),hidden:({props:e})=>!e?.selectedMonomers||!cc(e?.selectedMonomers),separator:p},{name:fc.modifyAminoAcids,title:"Modify amino acids",disabled:!1,hidden:!d.length,subMenuItems:d},{name:"layout_circular",title:"Arrange as a Ring",disabled:h,hidden:!u},{name:"edit_attachment_points",title:"Edit Attachment Points...",disabled:({props:e})=>!c(e),separator:!0},{name:"delete",title:"Delete",icon:(0,n.Y)(f.In,{name:"deleteMenu"}),disabled:({props:e={}})=>l(e)}],g=document.querySelector(f.tS);return g&&(0,H.createPortal)((0,n.Y)(ui,{id:li.FOR_SELECTED_MONOMERS,handleMenuChange:({id:e,props:t})=>{switch(!0){case"layout_circular"===e:i?.events.layoutCircular.dispatch(),a();break;case"copy"===e:i?.events.copySelectedStructure.dispatch();break;case"create_antisense_rna_chain"===e:i?.events.createAntisenseChain.dispatch(!1);break;case"create_antisense_dna_chain"===e:i?.events.createAntisenseChain.dispatch(!0);break;case"delete"===e:i?.events.deleteSelectedStructure.dispatch();break;case"paste"===e:i?.events.pasteFromClipboard.dispatch();break;case"edit_attachment_points"===e:{const e=t?.polymerBondRenderer?.polymerBond;if(!e)return;i?.events.openMonomerConnectionModal.dispatch({firstMonomer:e.firstMonomer,secondMonomer:e.secondMonomer,polymerBond:e,isReconnectionDialog:!0});break}case e?.startsWith(uc):{const t=e?.replace(uc,"");i?.events.modifyAminoAcids.dispatch({monomers:s,modificationType:t});break}}},menuItems:m}),g)},vu=(0,g.A)(f.$n)(({theme:e,isActive:t})=>({width:"40px",height:"31px",backgroundColor:t?e.ketcher.color.button.group.active:"white",marginRight:"8px",border:t?e.ketcher.outline.selected.color:e.ketcher.outline.small,borderRadius:"4px",outline:"none",":hover":{backgroundColor:t?e.ketcher.color.button.group.hover:"white"},":hover svg":{fill:t?"white":e.ketcher.color.button.group.active}})),wu=()=>{const e=Dr(kt),[t,o]=(0,i.useState)(!0),r="sequence-layout-mode"===Er(),[a,s]=(0,i.useState)(Boolean(e?.drawingEntitiesManager?.hasAntisenseChains)),l=(0,i.useRef)(t);return(0,i.useEffect)(()=>{const t=()=>{const t=Boolean(e?.drawingEntitiesManager?.hasAntisenseChains);s(t),r&&t&&e?.events.toggleIsSequenceSyncEditMode.dispatch(l.current)};return t(),e?.events.modelChange.add(t),()=>{e?.events.modelChange.remove(t)}},[e,r]),r&&a?(0,n.Y)(vu,{isActive:t,onClick:()=>{const n=!t;l.current=n,o(n),e?.events.toggleIsSequenceSyncEditMode.dispatch(n),Hd()},"data-testid":"sync_sequence_edit_mode","data-isactive":t,children:(0,n.FD)("svg",{width:"24",height:"24",viewBox:"0 0 24 24",xmlns:"http://www.w3.org/2000/svg",fill:t?"white":"#333333",children:[(0,n.Y)("rect",{x:"1",y:"1.5",width:"22",height:"2",rx:"1"}),(0,n.Y)("rect",{x:"2.5",y:"1.5",width:"5",height:"1.5",rx:"0.75",transform:"rotate(90 2.5 1.5)"}),(0,n.Y)("rect",{x:"9.33333",y:"1.5",width:"5",height:"1.5",rx:"0.75",transform:"rotate(90 9.33333 1.5)"}),(0,n.Y)("rect",{x:"16.1667",y:"1.5",width:"5",height:"1.5",rx:"0.75",transform:"rotate(90 16.1667 1.5)"}),(0,n.Y)("rect",{x:"23",y:"1.5",width:"5",height:"1.5",rx:"0.75",transform:"rotate(90 23 1.5)"}),(0,n.Y)("path",{d:"M2.16873 14.2715C2.08963 14.3652 2.02518 14.4253 1.97537 14.4517C1.9285 14.478 1.8699 14.4912 1.79959 14.4912C1.66189 14.4912 1.55057 14.4458 1.46561 14.355C1.38357 14.2612 1.34256 14.1074 1.34256 13.8936V13.2871C1.34256 13.0703 1.38357 12.9165 1.46561 12.8257C1.55057 12.7319 1.66189 12.6851 1.79959 12.6851C1.90506 12.6851 1.99295 12.7129 2.06326 12.7686C2.1365 12.8242 2.19217 12.918 2.23025 13.0498C2.26834 13.1787 2.30789 13.2666 2.34891 13.3135C2.43387 13.4043 2.58475 13.4966 2.80154 13.5903C3.01834 13.6841 3.25564 13.731 3.51346 13.731C3.91482 13.731 4.24441 13.6372 4.50223 13.4497C4.66629 13.3354 4.74832 13.1948 4.74832 13.0278C4.74832 12.9165 4.70877 12.8125 4.62967 12.7158C4.55057 12.6162 4.42166 12.5342 4.24295 12.4697C4.12576 12.4258 3.86355 12.3657 3.45633 12.2896C2.96414 12.1987 2.59207 12.0889 2.34012 11.96C2.08816 11.8311 1.88895 11.6494 1.74246 11.415C1.59598 11.1807 1.52273 10.9272 1.52273 10.6548C1.52273 10.2241 1.70291 9.84766 2.06326 9.52539C2.42361 9.2002 2.89236 9.0376 3.46951 9.0376C3.70096 9.0376 3.91482 9.06396 4.11111 9.1167C4.31033 9.1665 4.49051 9.24414 4.65164 9.34961C4.76883 9.23535 4.88602 9.17822 5.0032 9.17822C5.13504 9.17822 5.24197 9.2251 5.324 9.31885C5.40897 9.40967 5.45145 9.56201 5.45145 9.77588V10.4526C5.45145 10.6694 5.40897 10.8247 5.324 10.9185C5.24197 11.0093 5.13504 11.0547 5.0032 11.0547C4.89188 11.0547 4.7952 11.021 4.71316 10.9536C4.64871 10.9038 4.60037 10.8042 4.56814 10.6548C4.53592 10.5054 4.4949 10.3984 4.4451 10.334C4.36014 10.2227 4.2327 10.1289 4.06277 10.0527C3.89285 9.97656 3.69656 9.93848 3.47391 9.93848C3.14871 9.93848 2.8909 10.0146 2.70047 10.167C2.51297 10.3164 2.41922 10.4731 2.41922 10.6372C2.41922 10.7485 2.4573 10.8569 2.53348 10.9624C2.61258 11.0649 2.72684 11.1455 2.87625 11.2041C2.97586 11.2451 3.25564 11.311 3.71561 11.4019C4.1785 11.4927 4.53299 11.5923 4.77908 11.7007C5.02811 11.8091 5.23465 11.979 5.39871 12.2104C5.56277 12.4419 5.6448 12.7173 5.6448 13.0366C5.6448 13.4819 5.48807 13.8379 5.17459 14.1045C4.75857 14.4561 4.2283 14.6318 3.58377 14.6318C3.33475 14.6318 3.09158 14.6011 2.85428 14.5396C2.6199 14.481 2.39139 14.3916 2.16873 14.2715ZM9.615 12.2632V13.5991H10.2214C10.4382 13.5991 10.5921 13.6416 10.6829 13.7266C10.7766 13.8086 10.8235 13.917 10.8235 14.0518C10.8235 14.1836 10.7766 14.292 10.6829 14.377C10.5921 14.459 10.4382 14.5 10.2214 14.5H8.10768C7.89381 14.5 7.74 14.459 7.64625 14.377C7.55543 14.292 7.51002 14.1821 7.51002 14.0474C7.51002 13.9155 7.55543 13.8086 7.64625 13.7266C7.74 13.6416 7.89381 13.5991 8.10768 13.5991H8.71412V12.2632L7.22438 10.0747C7.02516 10.0747 6.87867 10.0322 6.78492 9.94727C6.6941 9.8623 6.64869 9.75391 6.64869 9.62207C6.64869 9.4873 6.6941 9.37891 6.78492 9.29688C6.87867 9.21191 7.03395 9.16943 7.25074 9.16943L8.06813 9.17383C8.28492 9.17383 8.43873 9.21484 8.52955 9.29688C8.6233 9.37891 8.67018 9.4873 8.67018 9.62207C8.67018 9.82422 8.55152 9.9751 8.31422 10.0747L9.16676 11.3315L10.0017 10.0747C9.86988 10.0249 9.77613 9.96191 9.72047 9.88574C9.6648 9.80957 9.63697 9.72168 9.63697 9.62207C9.63697 9.4873 9.68238 9.37891 9.7732 9.29688C9.86695 9.21484 10.0222 9.17236 10.239 9.16943L11.0872 9.17383C11.304 9.17383 11.4578 9.21484 11.5486 9.29688C11.6423 9.37891 11.6892 9.4873 11.6892 9.62207C11.6892 9.75684 11.6423 9.8667 11.5486 9.95166C11.4548 10.0337 11.3025 10.0747 11.0916 10.0747L9.615 12.2632ZM13.7082 10.8745V13.5991H14.0334C14.2502 13.5991 14.404 13.6416 14.4949 13.7266C14.5886 13.8086 14.6355 13.917 14.6355 14.0518C14.6355 14.1836 14.5886 14.292 14.4949 14.377C14.404 14.459 14.2502 14.5 14.0334 14.5H12.8513C12.6345 14.5 12.4792 14.459 12.3855 14.377C12.2947 14.292 12.2493 14.1821 12.2493 14.0474C12.2493 13.9185 12.2947 13.8115 12.3855 13.7266C12.4763 13.6416 12.6169 13.5991 12.8074 13.5991V10.0747H12.6667C12.4499 10.0747 12.2947 10.0337 12.2009 9.95166C12.1101 9.8667 12.0647 9.75684 12.0647 9.62207C12.0647 9.4873 12.1101 9.37891 12.2009 9.29688C12.2947 9.21191 12.4499 9.16943 12.6667 9.16943L13.7082 9.17383L16.011 12.7861V10.0747H15.6858C15.469 10.0747 15.3137 10.0337 15.22 9.95166C15.1291 9.8667 15.0837 9.75684 15.0837 9.62207C15.0837 9.4873 15.1291 9.37891 15.22 9.29688C15.3137 9.21191 15.469 9.16943 15.6858 9.16943L16.8679 9.17383C17.0847 9.17383 17.2385 9.21484 17.3293 9.29688C17.4231 9.37891 17.47 9.4873 17.47 9.62207C17.47 9.75391 17.4246 9.8623 17.3337 9.94727C17.2429 10.0322 17.1038 10.0747 16.9163 10.0747V14.5H16.0242L13.7082 10.8745ZM21.9236 9.40234C21.9792 9.32617 22.0393 9.26904 22.1037 9.23096C22.1711 9.19287 22.2429 9.17383 22.3191 9.17383C22.4509 9.17383 22.5578 9.21924 22.6399 9.31006C22.7248 9.40088 22.7673 9.55469 22.7673 9.77148V10.5361C22.7673 10.7529 22.7248 10.9082 22.6399 11.002C22.5578 11.0928 22.4509 11.1382 22.3191 11.1382C22.1989 11.1382 22.1023 11.1045 22.029 11.0371C21.9558 10.9697 21.9016 10.8438 21.8664 10.6592C21.8459 10.5361 21.8049 10.4409 21.7434 10.3735C21.6233 10.2417 21.4548 10.1362 21.238 10.0571C21.0241 9.97803 20.8088 9.93848 20.592 9.93848C20.3225 9.93848 20.0749 9.99707 19.8493 10.1143C19.6237 10.2314 19.4245 10.4219 19.2517 10.6855C19.0788 10.9492 18.9924 11.2627 18.9924 11.626V12.2104C18.9924 12.644 19.1491 13.0059 19.4626 13.2959C19.779 13.5859 20.217 13.731 20.7766 13.731C21.1106 13.731 21.3933 13.6855 21.6247 13.5947C21.7595 13.542 21.903 13.438 22.0554 13.2827C22.1491 13.189 22.2224 13.1289 22.2751 13.1025C22.3279 13.0732 22.3879 13.0586 22.4553 13.0586C22.5754 13.0586 22.6809 13.104 22.7717 13.1948C22.8625 13.2856 22.9079 13.3926 22.9079 13.5156C22.9079 13.6387 22.8464 13.7705 22.7234 13.9111C22.5446 14.1162 22.3147 14.2773 22.0334 14.3945C21.6555 14.5527 21.238 14.6318 20.781 14.6318C20.2478 14.6318 19.7673 14.522 19.3396 14.3022C18.9939 14.1265 18.6994 13.8496 18.4563 13.4717C18.2131 13.0908 18.0915 12.6763 18.0915 12.228V11.6172C18.0915 11.1484 18.1999 10.7119 18.4167 10.3076C18.6364 9.90039 18.9397 9.58691 19.3264 9.36719C19.7131 9.14746 20.1233 9.0376 20.5569 9.0376C20.8176 9.0376 21.0608 9.06836 21.2863 9.12988C21.5149 9.18848 21.7273 9.2793 21.9236 9.40234Z"}),(0,n.Y)("rect",{x:"23",y:"22.5",width:"22",height:"2",rx:"1",transform:"rotate(-180 23 22.5)"}),(0,n.Y)("rect",{x:"21.5",y:"22.5",width:"5",height:"1.5",rx:"0.75",transform:"rotate(-90 21.5 22.5)"}),(0,n.Y)("rect",{x:"14.6667",y:"22.5",width:"5",height:"1.5",rx:"0.75",transform:"rotate(-90 14.6667 22.5)"}),(0,n.Y)("rect",{x:"7.83333",y:"22.5",width:"5",height:"1.5",rx:"0.75",transform:"rotate(-90 7.83333 22.5)"}),(0,n.Y)("rect",{x:"1",y:"22.5",width:"5",height:"1.5",rx:"0.75",transform:"rotate(-90 1 22.5)"})]})}):null},Au=(e,t)=>{const o=(0,i.useRef)(null);(0,i.useLayoutEffect)(()=>{const n=e.current;if(n)return o.current=requestAnimationFrame(()=>{n.style.transform=`translateX(${t}px)`}),()=>{o.current&&(cancelAnimationFrame(o.current),o.current=null)}},[e,t])};var Cu={rulerArea:"RulerArea-module_rulerArea__Kix1O",rulerInput:"RulerArea-module_rulerInput__nCNOa",rulerInputDragging:"RulerArea-module_rulerInputDragging__saoOk",rulerScale:"RulerArea-module_rulerScale__4YOKH",rulerHandle:"RulerArea-module_rulerHandle__g4GPl"};var Mu=(0,i.memo)(({lineLengthValue:e,offsetX:t,isDragging:o,layoutMode:r,onCommitValue:a})=>{const s=(0,i.useRef)(null);Au(s,t);const l=e.toString(),[d,c]=(0,i.useState)(null),p=null!==d?d:l;return(0,n.Y)("input",{className:(0,V.A)(Cu.rulerInput,o&&Cu.rulerInputDragging),title:"Number of monomers in a line",type:"text",inputMode:"numeric",pattern:"[0-9]*",value:p,onChange:e=>{c(e.target.value)},onBlur:()=>{if(!d||""===d.trim())return void c(null);const e=Number(d);if(Number.isNaN(e)||e<1)return void c(null);const t="sequence-layout-mode"===r?10*Math.round(e/10):e;c(null),a(t)},onKeyDown:e=>{"Enter"===e.key&&(e.preventDefault(),e.currentTarget.blur())},"data-testid":"ruler-input",disabled:o,ref:s})});const ku=c.XKT;var Iu=(0,i.memo)(({transform:e,layoutMode:t})=>{const[o,r]=(0,i.useState)(null),a=e.k-.5<Number.EPSILON,s=(e,t,o,n)=>{const r=Math.max(0,Math.floor((e-n)/o)),i=Math.ceil((t-n)/o)+10;return Array.from({length:i-r},(e,t)=>n+(r+t)*o)},l=(0,i.useMemo)(()=>{const n=o?.ownerSVGElement?.width.baseVal.value||1e3,r=e.invertX(0),i=e.invertX(n);if("sequence-layout-mode"===t){return s(r,i,210,40)}return"snake-layout-mode"===t?s(r,i,ku,25):[]},[t,o,e]),d=(0,i.useMemo)(()=>{const o=[];return l.forEach((r,i)=>{if("sequence-layout-mode"===t)o.push((0,n.Y)("line",{x1:e.applyX(r),y1:14,x2:e.applyX(r),y2:22,stroke:"#7C7C7F",strokeWidth:1},`ruler-mark-${r}`));else if("snake-layout-mode"===t)if(a){i%5===0?o.push((0,n.Y)("text",{x:e.applyX(r),y:18,fontSize:10,fontWeight:500,fill:"#7C7C7F",textAnchor:"middle",dominantBaseline:"middle",children:i},`ruler-label-${r}`)):o.push((0,n.Y)("line",{x1:e.applyX(r),y1:14,x2:e.applyX(r),y2:22,stroke:"#7C7C7F",strokeWidth:1},`ruler-mark-${r}`))}else o.push((0,n.Y)("text",{x:e.applyX(r),y:18,fontSize:10,fontWeight:500,fill:"#7C7C7F",textAnchor:"middle",dominantBaseline:"middle",children:i},`ruler-label-${r}`));const s=l[i+1];void 0!==s&&(a&&"snake-layout-mode"===t||o.push((0,n.Y)("line",{x1:e.applyX(r+10),y1:18,x2:e.applyX(s-10),y2:18,stroke:"#B4B9D6",strokeDasharray:"2,2",strokeWidth:1},`ruler-fill-${r}-${s}`)))}),o},[l,t,e,a]);return(0,n.Y)("svg",{className:Cu.rulerScale,ref:r,"data-testid":"ruler-scale",children:d})});var Su=(0,i.memo)(({offsetX:e,onDragStart:t,onDrag:o,onDragEnd:r})=>{const a=(0,i.useRef)(null),s=(0,i.useRef)(null);return Au(a,e),(0,i.useEffect)(()=>{if(!s.current)return;const e=(0,$.Ltv)(s.current),n=(0,$.$Er)().on("start",t).on("drag",o).on("end",r);return e.call(n),()=>{e.on(".drag",null)}},[o,r,t]),(0,n.Y)("svg",{xmlns:"http://www.w3.org/2000/svg",className:Cu.rulerHandle,viewBox:"0 0 16 13",fill:"none",pointerEvents:"none",ref:a,"data-testid":"ruler-handle",children:(0,n.FD)("g",{cursor:"pointer",pointerEvents:"all",ref:s,children:[(0,n.Y)("mask",{id:"ruler-handle-mask",fill:"#fff",children:(0,n.Y)("path",{fillRule:"evenodd",d:"M16 1.625a1 1 0 0 0-1-1H1a1 1 0 0 0-1 1v4.5a1 1 0 0 0 .4.8l7 5.25a1 1 0 0 0 1.2 0l7-5.25a1 1 0 0 0 .4-.8v-4.5Z",clipRule:"evenodd"})}),(0,n.Y)("path",{fill:"#CAD3DD",fillRule:"evenodd",d:"M16 1.625a1 1 0 0 0-1-1H1a1 1 0 0 0-1 1v4.5a1 1 0 0 0 .4.8l7 5.25a1 1 0 0 0 1.2 0l7-5.25a1 1 0 0 0 .4-.8v-4.5Z",clipRule:"evenodd"}),(0,n.Y)("path",{fill:"#B4B9D6",d:"m15.6 6.925-.6-.8.6.8Zm-8.2 5.25.6-.8-.6.8Zm-7-5.25.6-.8-.6.8Zm.6-5.3h14v-2H1v2Zm0 4.5v-4.5h-2v4.5h2Zm7 5.25-7-5.25-1.2 1.6 7 5.25 1.2-1.6Zm7-5.25-7 5.25 1.2 1.6 7-5.25-1.2-1.6Zm0-4.5v4.5h2v-4.5h-2Zm1.2 6.1a2 2 0 0 0 .8-1.6h-2l1.2 1.6Zm-9.4 5.25a2 2 0 0 0 2.4 0l-1.2-1.6-1.2 1.6ZM-1 6.125a2 2 0 0 0 .8 1.6l1.2-1.6h-2Zm16-4.5h2a2 2 0 0 0-2-2v2Zm-14-2a2 2 0 0 0-2 2h2v-2Z",mask:"url(#ruler-handle-mask)"})]})})});const Yu=()=>{const e=Dr(kt),t=e?.zoomTool,[o,n]=(0,i.useState)(new $.uVw(1,0,0));return(0,i.useEffect)(()=>{if(!t)return;const e=e=>{e&&requestAnimationFrame(()=>{n(e)})};return t.subscribeOnZoomEvent(e),()=>{t.unsubscribeOnZoomEvent(e)}},[t]),o},Lu=()=>{const e=Er(),{width:t}=(0,i.useContext)(Br),o=(0,r.d4)(zt)[e],a=(0,r.d4)(kt),s=(0,i.useRef)(0),[l,d]=(0,i.useState)(0),[c,p]=(0,i.useState)(!1),u=Yu(),h=o/10-1,m=(0,i.useMemo)(()=>{if("sequence-layout-mode"===e){const e=210;return 40+Math.floor(o/10)*e}return"snake-layout-mode"===e?25+o*ku:0},[e,o]),[g,f]=(0,i.useMemo)(()=>{const e=u.applyX(m)+l,o=e-8;let n=e+10;const r=a?.canvas.parentElement,i=r?.clientWidth||t;if(!i)return[n,o];const s=r?.scrollLeft||0,d=s+i;return n+35>d&&(n=d-35),n<s&&(n=s),[n,o]},[a?.canvas.parentElement,t,u,m,l]),b=(0,i.useCallback)(t=>{a?.events.setEditorLineLength.dispatch({[e]:t})},[a?.events?.setEditorLineLength,e]),x=(0,i.useCallback)(t=>{if("sequence-layout-mode"===e){const e=(t-10*h-40)/20;return Math.max(10,10*Math.round(e/10))}if("snake-layout-mode"===e){const e=(t-25)/ku;return Math.max(1,Math.round(e))}return o},[e,h,o]),y=(0,i.useCallback)(e=>{const t=e-s.current,o=u.applyX(m)+t;return[t,u.invertX(o)]},[u,m]),v=(0,i.useMemo)(()=>{if(!c)return o;const[,e]=y(s.current+l);return x(e)},[c,o,y,s,l,x]),w=(0,i.useCallback)(e=>{p(!0),s.current=e.sourceEvent.clientX,a?.events.toggleLineLengthHighlighting.dispatch(!0,m)},[a?.events?.toggleLineLengthHighlighting,m]),A=(0,i.useCallback)(e=>{const[t,o]=y(e.sourceEvent.clientX);d(t),a?.events.toggleLineLengthHighlighting.dispatch(!0,o)},[a?.events?.toggleLineLengthHighlighting,y]),C=(0,i.useCallback)(e=>{p(!1);const[,t]=y(e.sourceEvent.clientX),n=x(t);n!==o&&b(n),d(0),s.current=0,a?.events.toggleLineLengthHighlighting.dispatch(!1)},[y,x,o,a?.events.toggleLineLengthHighlighting,b]);if("flex-layout-mode"===e)return null;return!window._ketcher_isChainLengthRulerDisabled?(0,n.FD)("div",{className:(0,V.A)(Cu.rulerArea,c&&Cu.rulerAreaDragging),"data-testid":"ruler-area",children:[(0,n.Y)(Mu,{lineLengthValue:c?v:o,offsetX:g,isDragging:c,layoutMode:e,onCommitValue:b}),(0,n.Y)(Su,{offsetX:f,onDragStart:w,onDrag:A,onDragEnd:C}),(0,n.Y)(Iu,{transform:u,layoutMode:e})]}):null};var Du="DragGhost-module_dragGhost__m5lHf";const Eu=({preset:e})=>{const{sugar:t,phosphate:o,base:r}=e;if(!t)return null;const[i,a]=(0,c.w7j)(t),s=new a(new i(t)),l=o?(()=>{const[e,t]=(0,c.w7j)(o);return new t(new e(o))})():null,d=r?(()=>{const[e,t]=(0,c.w7j)(r);return new t(new e(r))})():null,p=s.monomerSize,u=l?.monomerSize,h=d?.monomerSize,m="left"===(e.phosphatePosition??(0,c.uVP)(e)),g=u&&m?u.width+30:0;let f=0;u&&(f=m?0:g+p.width+30);const b=g+(p.width-(h?.width||0))/2,x=0+p.height+30,y=Math.max(g+p.width,u?f+u.width:0,h?b+h.width:0),v=Math.max(0+p.height,u?0+u.height:0,h?x+h.height:0),w=(p.width-4)/p.width,A=(p.height-4)/p.height,C=Math.min(w,A),M=(p.width-p.width*C)/2,k=(p.height-p.height*C)/2,I=u?Math.min((u.width-4)/u.width,(u.height-4)/u.height):1,S=u?(u.width-u.width*I)/2:0,Y=u?(u.height-u.height*I)/2:0,L=h?Math.min((h.width-4)/h.width,(h.height-4)/h.height):1,D=h?(h.width-h.width*L)/2:0,E=h?(h.height-h.height*L)/2:0;return(0,n.Y)("svg",{xmlns:"http://www.w3.org/2000/svg",width:y,height:v,viewBox:`0 0 ${y} ${v}`,overflow:"visible",children:(0,n.FD)("g",{style:{filter:"drop-shadow(0px 2px 2px rgba(0, 0, 0, 0.4))"},children:[(0,n.FD)("g",{transform:`translate(${g}, 0)`,children:[(0,n.Y)("use",{href:s.monomerSymbolElementId,fill:"white"}),(0,n.Y)("g",{transform:`translate(${M}, ${k}) scale(${C})`,children:(0,n.Y)("use",{href:s.monomerSymbolElementId,fill:"#CAD3DD"})}),(0,n.Y)("text",{x:p.width/2,y:p.height/2,textAnchor:"middle",dominantBaseline:"central",pointerEvents:"none",fill:"#333",fontSize:"7px",fontWeight:"bold",children:t.label})]}),u&&(0,n.FD)("g",{transform:`translate(${f}, 0)`,children:[(0,n.Y)("use",{href:l.monomerSymbolElementId,fill:"white"}),(0,n.Y)("g",{transform:`translate(${S}, ${Y}) scale(${I})`,children:(0,n.Y)("use",{href:l.monomerSymbolElementId,fill:"#CAD3DD"})}),(0,n.Y)("text",{x:u.width/2,y:u.height/2,textAnchor:"middle",dominantBaseline:"central",pointerEvents:"none",fill:"#333",fontSize:"7px",fontWeight:"bold",children:o?.label})]}),h&&(0,n.FD)("g",{transform:`translate(${b}, ${x})`,children:[(0,n.Y)("use",{href:d.monomerSymbolElementId,fill:"white"}),(0,n.Y)("g",{transform:`translate(${D}, ${E}) scale(${L})`,children:(0,n.Y)("use",{href:d.monomerSymbolElementId,fill:"#CAD3DD"})}),(0,n.Y)("text",{x:h.width/2,y:h.height/2,textAnchor:"middle",dominantBaseline:"central",pointerEvents:"none",fill:"#333",fontSize:"7px",fontWeight:"bold",children:r?.label})]}),u&&(0,n.FD)("g",{children:[(0,n.Y)("line",{x1:m?f+u.width:g+p.width,y1:0+p.height/2,x2:m?g+2:f+2,y2:0+u.height/2,stroke:"white",strokeWidth:7,strokeLinecap:"butt"}),(0,n.Y)("line",{x1:m?f+u.width-3:g+p.width-3,y1:0+p.height/2,x2:m?g+3:f+3,y2:0+u.height/2,stroke:"#CAD3DD",strokeWidth:4,strokeLinecap:"round"})]}),h&&(0,n.FD)("g",{children:[(0,n.Y)("line",{x1:g+p.width/2,y1:0+p.height,x2:b+h.width/2,y2:x+3,stroke:"white",strokeWidth:7,strokeLinecap:"butt"}),(0,n.Y)("line",{x1:g+p.width/2,y1:0+p.height-3,x2:b+h.width/2,y2:x+3,stroke:"#CAD3DD",strokeWidth:4,strokeLinecap:"round"})]})]})})},Fu=({monomerItem:e})=>{const t=(0,i.useMemo)(()=>{if((0,c.eoH)(e)){const t=new c.cN8(e);return new c.TYn(t)}{const[t,o]=(0,c.w7j)(e);return new o(new t(e))}},[e]),o=t.monomerSymbolElementId,r=t.monomerSize,{width:a,height:s}=r;return(0,n.FD)("svg",{xmlns:"http://www.w3.org/2000/svg",width:a,height:s,viewBox:`0 0 ${a} ${s}`,overflow:"visible",children:[(0,n.Y)("use",{href:o,fill:"#CAD3DD",stroke:"white",strokeWidth:2,style:{filter:"drop-shadow(0px 2px 2px rgba(0, 0, 0, 0.4))"}}),(0,n.Y)("text",{x:a/2,y:s/2,textAnchor:"middle",dominantBaseline:"central",pointerEvents:"none",fill:"#333",fontSize:"7px",fontWeight:"bold",children:e.label})]})},Nu=()=>{const e=(0,r.d4)(kt),[t,o]=(0,i.useState)(null),a=(0,i.useRef)(null),s=(0,i.useRef)(null),l=(0,i.useRef)(null),d=Yu();(0,i.useEffect)(()=>{if(!e)return;const t=e=>{o(e)};return e.events.setLibraryItemDragState.add(t),()=>{e.events.setLibraryItemDragState.remove(t)}},[e]),(0,i.useEffect)(()=>{if(!c.OQ2.instance||!t)return;const e=c.OQ2.instance.canvasWrapper.node();e&&(l.current=e.getBoundingClientRect())},[t]);const p=e?.ketcherRootElementBoundingClientRect?.left||0,u=e?.ketcherRootElementBoundingClientRect?.top||0;return(0,i.useLayoutEffect)(()=>{const e=a.current;if(!e||!t)return;const{x:o,y:n}=t.position,r=l.current,i=r&&o+p>=r.left&&o+p<=r.right&&n+u>=r.top&&n+u<=r.bottom;return s.current=requestAnimationFrame(()=>{if(i){const t=d.k;e.style.transformOrigin="0 0",e.style.transform=`translate(${o}px, ${n}px) scale(${t})`}else e.style.transform=`translate(${o}px, ${n}px)`}),()=>{s.current&&(cancelAnimationFrame(s.current),s.current=null)}},[p,t,u,d.k]),t?(0,n.Y)("div",{className:Du,ref:a,"data-testid":"drag-ghost",children:(0,c.jT0)(t.item)?(0,n.Y)(Eu,{preset:t.item}):(0,n.Y)(Fu,{monomerItem:t.item})}):null};var Pu,Bu,Tu,Ru,Ou;function zu(){return zu=Object.assign?Object.assign.bind():function(e){for(var t=1;t<arguments.length;t++){var o=arguments[t];for(var n in o)({}).hasOwnProperty.call(o,n)&&(e[n]=o[n])}return e},zu.apply(null,arguments)}var $u=function(e){return i.createElement("svg",zu({xmlns:"http://www.w3.org/2000/svg",width:22,height:24,fill:"none",viewBox:"0 0 22 24"},e),Pu||(Pu=i.createElement("path",{fill:"transparent",d:"M0 0h21.503v24H0z"})),Bu||(Bu=i.createElement("path",{fill:"#676767",d:"m13.507 18.936-2.04-1.92c-.7-.659-1.403-1.317-2.108-1.983v3.097q0 .645.007 1.289c0 .049-.007.09-.007.126v2.04c0 .834 0 1.557.003 2.02l.278.163c.719.3 1.434.307 2.151.033l4.264-2.469c-.358-.326-1.395-1.31-2.548-2.396M6.159 19.39v-1.091L6.163 2.23v-.002L1.26 5.059C.421 5.647.002 6.482.002 7.553c0 2.944.006 5.888-.002 8.831-.003 1.08.398 1.913 1.212 2.517l4.946 2.856v-2.368"})),Tu||(Tu=i.createElement("path",{fill:"url(#paint0_linear_5175_84899)",d:"m18.642 4.137 1.672.966c.785.58 1.187 1.385 1.188 2.41v8.974c0 1-.385 1.79-1.133 2.367l-1.423.822h-.004c-.097-.097-6.384-6.375-6.513-6.386.044-.044.56-.582 1.278-1.33L20.3 5.097z"})),Ru||(Ru=i.createElement("path",{fill:"url(#paint1_linear_5175_84899)",d:"M17.335 3.383 11.849.216c-.718-.29-1.455-.29-2.219.011l-.27.139s0 10.346.041 11.106c.341-.346 7.808-7.96 7.934-8.09"})),Ou||(Ou=i.createElement("defs",null,i.createElement("linearGradient",{id:"paint0_linear_5175_84899",x1:19.854,x2:11.579,y1:4.363,y2:13.111,gradientUnits:"userSpaceOnUse"},i.createElement("stop",{stopColor:"#3BC2D7"}),i.createElement("stop",{offset:.74,stopColor:"#1D9DB1"})),i.createElement("linearGradient",{id:"paint1_linear_5175_84899",x1:19.854,x2:11.579,y1:4.363,y2:13.111,gradientUnits:"userSpaceOnUse"},i.createElement("stop",{stopColor:"#3BC2D7"}),i.createElement("stop",{offset:.74,stopColor:"#1D9DB1"})))))};const ju=(0,g.A)("div")(({theme:e})=>({width:"430px",minHeight:"260px",padding:"0 18px",borderRadius:"6px",fontSize:"12px",color:e.ketcher.color.text.primary,fontWeight:e.ketcher?.font?.weight?.regular,a:{color:"#167782"},".body":{borderRadius:"6px",padding:"5px 65px",overflowY:"auto",overflowX:"hidden",".versionName":{fontWeight:400,marginBottom:"2px"},".firstline":{display:"inline-block"},".links":{textAlign:"right"},".indigoVersion":{marginTop:"20px",display:"flex",gap:"2px"}},".headerContent":{display:"flex",justifyContent:"space-between",padding:"0 30px",a:{display:"flex",alignItems:"center",gap:"10px",textDecoration:"none",color:e.ketcher?.color?.text?.primary},".title":{fontSize:"20px",lineHeight:"22px"}},".indigoFirstLine":{display:"inline-block"},dd:{margin:0,marginBottom:"0.2em"},dt:{marginTop:"3px"},".okButton":{border:"1px solid #333333",backgroundColor:"#FFFFFF",color:"#333333",display:"inline-flex",justifyContent:"center",alignItems:"center",outline:"none",minWidth:"70px",lineHeight:"14px",height:"24px",borderRadius:"4px",fontSize:"10px","&:hover":{color:"#333333",border:"1px solid  #333333"},"&:active":{color:"#333333",border:"1px solid #333333"},"&:disabled":{color:"rgba(51, 51, 51, 0.7)",border:"1px solid rgba(51, 51, 51, 0.7)"}},".aboutFooter":{borderTop:"1px solid #e1e5ea",margin:0,padding:"15px 0",display:"flex",justifyContent:"flex-end",alignItems:"center"}})),_u="https://lifescience.opensource.epam.com/ketcher/index.html";function Hu({isOpen:e,onClose:t}){const o=Lr(),{buildDate:r,indigoVersion:i,version:a}=Dr($t),s=function(e=""){if(!e.includes("T"))return e;const[t,o]=e.split("T");return`${t}; ${o}`}(r),l=()=>{o({type:"MODAL_CLOSE"}),t()};return(0,n.Y)(wa,{title:"",isOpen:e,onClose:l,hideHeaderBorder:!0,children:(0,n.Y)(wa.Content,{children:(0,n.FD)(ju,{children:[(0,n.Y)("div",{className:"headerContent",children:(0,n.FD)("a",{href:_u,target:"_blank",rel:"noopener noreferrer",children:[(0,n.Y)($u,{}),(0,n.Y)("span",{className:"title",children:"Ketcher"})]})}),(0,n.Y)("div",{className:"body",children:(0,n.FD)("dl",{children:[(0,n.Y)("dt",{"data-testid":"build-version",children:(0,n.FD)("a",{href:_u,target:"_blank",rel:"noopener noreferrer",children:["Version ",a]})}),(0,n.FD)("dd",{"data-testid":"build-time",children:["Build at ",(0,n.Y)("time",{children:s})]}),(0,n.FD)("div",{className:"infoLinks",children:[(0,n.Y)("dt",{children:(0,n.Y)("a",{href:"https://lifescience.opensource.epam.com/ketcher/#feedback",target:"_blank",rel:"noopener noreferrer",children:"Feedback"})}),(0,n.Y)("dt",{children:(0,n.Y)("a",{href:"https://lifescience.opensource.epam.com/",target:"_blank",rel:"noopener noreferrer",children:"EPAM Life Sciences"})})]}),(0,n.Y)("div",{className:"indigoVersion",children:(0,n.Y)("a",{href:"https://lifescience.opensource.epam.com/indigo/",target:"_blank",rel:"noopener noreferrer",children:"Indigo Toolkit"})}),(0,n.Y)("div",{"data-testid":"build-indigo-version",children:i?(0,n.FD)("dd",{children:["Version ",i]}):(0,n.Y)("p",{children:"Standalone"})})]})}),(0,n.Y)("div",{className:"aboutFooter",children:(0,n.Y)("button",{onClick:l,className:"okButton","data-testid":"ok-button",children:"Ok"})})]})})})}function qu(){const[e,t]=(0,i.useState)(!1),o={isOpen:e,onClose:()=>t(!1)};return(0,n.FD)(n.FK,{children:[(0,n.FD)("div",{style:{display:"flex",alignItems:"center"},children:[(0,n.Y)(f.K0,{iconName:"help",title:"Help (?)",onClick:()=>{window.open("https://github.com/epam/ketcher/blob/master/documentation/help.md#ketcher-macromolecules-mode","_blank")},testId:"help-button"}),(0,n.Y)(f.K0,{iconName:"about",title:"About",onClick:()=>t(!0),testId:"about-button"})]}),(0,n.Y)(Hu,{...o})]})}const Vu=g.A.div`
  position: absolute;
  left: ${e=>e.left}px;
  top: ${e=>e.top}px;
  display: flex;
  flex-direction: row;
  gap: 8px;
  transform: translate(-50%, -100%) translateY(-10px);
  z-index: 100;
`,Wu=g.A.button`
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  padding: 0;
  color: #2b2f3a;
  box-shadow: 0 6px 10px rgba(103, 104, 132, 0.15);

  &:hover {
    color: #188794;
  }

  &:active {
    background: #167782;
    color: #fff;
  }

  & > svg {
    width: 20px;
    height: 20px;
  }
`,Uu=()=>{const e=Dr(kt),[t,o]=(0,i.useState)(!1),[r,a]=(0,i.useState)(new c.ZY9(0,0)),s=(0,i.useRef)(new c.ZY9(0,0)),l=(0,i.useCallback)(()=>{if(!e)return;const t=e.drawingEntitiesManager?.getSelectedEntitiesBoundingBox();if(!t)return;const o=(t.left+t.right)/2,n=t.top-1.7,r=c.E$k.modelToView(new c.ZY9(o,n)),i=Math.abs(r.x-s.current.x),l=Math.abs(r.y-s.current.y);(i>.1||l>.1)&&(s.current=r,a(r))},[e]);(0,i.useEffect)(()=>{if(!e)return;const t=()=>{const t=(e.drawingEntitiesManager?.selectedEntitiesArr??[]).filter(e=>e instanceof c.NJ2||e instanceof c.rRu),n=e.selectedTool instanceof c.oJj&&("rotating"===e.selectedTool.mode||"moving"===e.selectedTool.mode);t.length>=2&&"sequence-layout-mode"!==e.mode.modeName&&e.selectedTool instanceof c.oJj&&!n?(l(),o(!0)):o(!1)};return e.events.selectEntities.add(t),()=>{e.events.selectEntities.remove(t)}},[e,l]),(0,i.useEffect)(()=>{if(!e||!t)return;let n=0;const r=()=>{e.selectedTool instanceof c.oJj&&("rotating"===e.selectedTool.mode||"moving"===e.selectedTool.mode)?o(!1):(l(),n=requestAnimationFrame(r))};return n=requestAnimationFrame(r),()=>cancelAnimationFrame(n)},[e,t,l]);return t?(0,n.FD)(Vu,{left:r.x,top:r.y,children:[(0,n.Y)(Wu,{onClick:()=>{e?.events.flipHorizontal.dispatch()},title:"Flip horizontally","data-testid":"transform-flip-h",children:(0,n.Y)(f.In,{name:"transform-flip-h"})}),(0,n.Y)(Wu,{onClick:()=>{e?.events.flipVertical.dispatch()},title:"Flip vertically","data-testid":"transform-flip-v",children:(0,n.Y)(f.In,{name:"transform-flip-v"})}),(0,n.Y)(Wu,{onClick:()=>{e?.events.deleteSelectedStructure.dispatch(),o(!1)},title:"Delete","data-testid":"float-delete",children:(0,n.Y)(f.In,{name:"delete"})})]}):null},Ku=(0,l.A)({});function Gu({onInit:e,ketcherId:t,theme:o,togglerComponent:l,monomersLibraryUpdate:c,monomersLibraryReplace:p,isMacromoleculesEditorTurnedOn:u}){const[h]=(0,i.useState)(()=>qn()),m=(0,i.useRef)(null),g=o?(0,d.merge)(Un,o):Un,b=(0,d.merge)(Ku,{ketcher:g});return(0,i.useEffect)(()=>{h.dispatch(it(t))},[t,h]),(0,n.Y)(r.Kq,{store:h,children:(0,n.FD)(s.a,{theme:b,children:[(0,n.Y)(a.mL,{styles:Kn}),(0,n.Y)(Tr,{rootRef:m,isMacromoleculesEditorTurnedOn:u,children:(0,n.Y)(Fd,{ref:m,className:f.$2,children:(0,n.Y)(Qu,{ketcherId:t,theme:g,togglerComponent:l,monomersLibraryUpdate:c,monomersLibraryReplace:p,onInit:e})})})]})})}function Qu({ketcherId:e,theme:t,togglerComponent:o,monomersLibraryUpdate:r,monomersLibraryReplace:a,onInit:s}){const l=Lr(),d=(0,i.useRef)(null),p=Dr(Ro),u=Dr(kt),h=Dr(Lt),m=function(){const e=Dr(Mt),[t,o]=(0,i.useState)(!1);let n;try{n=c.qd$.getKetcher(e)}catch(s){c.$yr.error(`Failed to get ketcher instance with id ${e}`,s)}const r=(0,i.useCallback)(()=>o(!0),[o]),a=(0,i.useCallback)(()=>o(!1),[o]);return(0,i.useEffect)(()=>(n?.eventBus.addListener(c.i0C.LOADING,r),n?.eventBus.addListener(c.i0C.SUCCESS,a),n?.eventBus.addListener(c.i0C.FAILURE,a),()=>{n?.eventBus.removeListener(c.i0C.LOADING,r),n?.eventBus.removeListener(c.i0C.SUCCESS,a),n?.eventBus.removeListener(c.i0C.FAILURE,a)}),[n?.eventBus,a,r]),t}(),[g,f]=(0,i.useState)(!1),b=Fr(),[x,y]=(0,i.useState)(),[v,w]=(0,i.useState)(),[A,C]=(0,i.useState)([]),[M,k]=(0,i.useState)(!0),I=(0,i.useCallback)(()=>{(0,c.s1T)().then(k)},[]),{show:S}=(0,q.EF)({id:li.FOR_SEQUENCE}),{show:Y}=(0,q.EF)({id:li.FOR_SELECTED_MONOMERS});(0,i.useEffect)(()=>(l(lt({ketcherId:e,theme:t,canvas:d.current,monomersLibraryUpdate:r,monomersLibraryReplace:a,onInit:s,onLibraryError:e=>{l(rt(e instanceof Error?e.message:"Failed to load monomers library"))}})),()=>{l(dt(null))}),[l]),function(){const e=Lr(),t=Dr(kt),o=Dr(Io);(0,i.useEffect)(()=>{if(!t)return;const n=t.monomersLibrary,r=o.length?o:t.defaultRnaPresetsLibraryItems,i=[...Ir(n,r,!0)];let a=_o(),s=[];const l=i.map(e=>e.name);if(a){for(const e of a){let t=0,o=e.name;for(;l.includes(o);)t++,o=`${e.name}${"_Copy".repeat(t)}`;o!==e.name&&qo({...e,name:o})}a=_o()??a,s=Ir(n,a)}return e(po(n)),e(ho(null)),e(Tn(i)),a&&e(Rn(s)),e(jn()),()=>{e(po([])),e(_n())}},[t,o,e])}(),(0,i.useEffect)(()=>{const e=(0,c.VAU)(e=>{if(e.defaultPrevented)return;const t=e.target;t&&("INPUT"===t.tagName||"TEXTAREA"===t.tagName||t.isContentEditable||t.closest('[contenteditable="true"]'))||("?"===e.key||"/"===e.key&&e.shiftKey)&&!e.repeat&&(e.preventDefault(),window.open("https://github.com/epam/ketcher/blob/master/documentation/help.md#ketcher-macromolecules-mode","_blank")?.focus?.())});return window.addEventListener("keydown",e),()=>window.removeEventListener("keydown",e)},[]),(0,i.useEffect)(()=>{u?.events.rightClickSequence.add(([e,t])=>{y(t),w(e),I(),window.dispatchEvent(new Event("hidePreview")),l(pt(!0)),S({event:e,props:{sequenceItemRenderer:e.target.__data__}})}),u?.events.rightClickPolymerBond.add(([e,t])=>{w(e),C([]),Y({event:e,props:{polymerBondRenderer:t}})}),u?.events.rightClickSelectedMonomers.add(([e,t])=>{C(t),w(e),I(),Y({event:e,props:{selectedMonomers:t}})}),u?.events.rightClickCanvas.add(([e,t])=>{w(e),I(),window.dispatchEvent(new Event("hidePreview")),l(pt(!0)),C(t),Y({event:e,props:{selectedMonomers:t}})}),u?.events.rightClickCanvasSequence.add(([e,t])=>{w(e),I(),window.dispatchEvent(new Event("hidePreview")),l(pt(!0)),y(t),S({event:e,props:{}})}),u?.events.toggleMacromoleculesPropertiesVisibility.add(()=>{l(mt({}))})},[u]),(0,i.useEffect)(()=>(u?.zoomTool.observeCanvasResize(),()=>{u?.zoomTool.destroy()}),[u]),(0,i.useEffect)(()=>{const e=e=>{const t=e.detail;t&&l(xt(t))};return window.addEventListener(c.wmv,e),()=>{window.removeEventListener(c.wmv,e)}},[l]);const L=(0,i.useCallback)(()=>{f(e=>!e)},[]);return(0,n.FD)(n.FK,{children:[(0,n.FD)(gr,{children:[(0,n.FD)(gr.Top,{shortened:!g,"data-testid":"top-toolbar",children:[(0,n.Y)(iu,{}),(0,n.FD)(Nd,{children:[(0,n.Y)(wu,{}),(0,n.Y)(nc,{}),(0,n.Y)(ip,{}),(0,n.Y)(Pd,{className:b?"toggler-component-wrapper--disabled":"",children:o}),(0,n.Y)(Jd,{}),(0,n.Y)(qu,{}),(0,n.Y)($d,{}),(0,n.Y)(Jd,{}),(0,n.Y)(fu,{})]})]}),(0,n.Y)(gr.Left,{children:(0,n.Y)(au,{})}),(0,n.FD)(gr.Main,{children:[(0,n.Y)(xu,{}),(0,n.Y)(Lu,{}),(0,n.FD)(Bd,{id:"polymer-editor-canvas","data-testid":"ketcher-canvas","data-canvasmode":"macromolecules-mode",preserveAspectRatio:"xMidYMid meet",ref:d,width:"100%",height:"100%",style:{overflow:"hidden",overflowClipMargin:"content-box"},children:[(0,n.FD)("defs",{children:[(0,n.Y)(Ad,{}),(0,n.Y)(wd,{}),(0,n.Y)(Cd,{}),(0,n.Y)(Md,{}),(0,n.Y)(kd,{}),(0,n.Y)(Id,{}),(0,n.Y)(Sd,{}),(0,n.Y)(Ld,{}),(0,n.Y)(Yd,{})]}),(0,n.Y)("g",{className:"drawn-structures","data-testid":"drawn-structures"}),h&&(0,n.Y)("rect",{x:0,y:0,width:"100%",height:"100%",fill:"transparent",pointerEvents:"all"})]}),(0,n.Y)(Uu,{}),m&&(0,n.Y)(Rd,{})]}),(0,n.Y)(gr.Right,{hide:g,children:(0,n.Y)(rs,{toggleLibraryVisibility:L})}),(0,n.Y)(gr.Bottom,{children:(0,n.Y)(ou,{})}),(0,n.Y)(gr.InsideRoot,{children:g&&(0,n.Y)(as,{onClick:L})})]}),(0,n.Y)(ep,{}),(0,n.Y)(Nu,{}),(0,n.Y)(bc,{selections:x,contextMenuEvent:v,isPasteAvailable:M}),(0,n.Y)(yu,{selectedMonomers:A,contextMenuEvent:v,isPasteAvailable:M}),(0,n.Y)(fd,{}),(0,n.Y)(Ed,{}),(0,n.Y)(z.A,{anchorOrigin:{vertical:"bottom",horizontal:"center"},open:p.length>0,children:(0,n.Y)(bd,{id:"error-tooltip-list","data-testid":"error-tooltip-list",children:p.map((e,t)=>(0,n.FD)(xd,{children:[(0,n.Y)(yd,{"data-testid":`error-tooltip-${t}`,children:e}),(0,n.Y)(vd,{testId:`error-tooltip-close-${t}`,iconName:"close",onClick:()=>(e=>{l(Eo(e))})(e)})]},e))})})]})}}}]);