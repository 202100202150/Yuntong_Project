import {defineStore} from 'pinia';import {http} from '@/api/http';
export interface User{id:string;username:string;displayName:string;role:string;enabled:boolean;permissions:string[]}
export const useAuthStore=defineStore('auth',{state:()=>({user:null as User|null,initialized:false,setupRequired:false,siteName:'校园监测'}),actions:{
async refresh(){const{data}=await http.get('/auth/status');this.user=data.user;this.setupRequired=data.setupRequired;this.siteName=typeof data.siteName==='string'&&data.siteName.trim()?data.siteName.trim():'校园监测';this.initialized=true},
async login(username:string,password:string,displayName?:string){const{data}=await http.post(this.setupRequired?'/auth/setup':'/auth/login',{username,password,displayName});this.user=data.user;this.setupRequired=false;this.initialized=true},
async logout(){await http.post('/auth/logout');this.user=null},can(name:string){return this.user?.permissions.includes(name)??false}}});
